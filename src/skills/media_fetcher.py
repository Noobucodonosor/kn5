"""
MediaFetcher: scarica contenuto Instagram e YouTube.

Instagram blocca le richieste anonime, quindi serve autenticazione via cookie:
in locale dal browser (Chrome loggato su IG), in cloud da un file cookies.txt.
  - Reel/video  → yt-dlp
  - Carosello/immagini → gallery-dl (yt-dlp non estrae le slide-immagine di IG)
  - Metadata    → yt-dlp (instaloader è rotto: doc_id GraphQL deprecato da IG)
Output: {media_type, assets, audio_path}; assets solo .jpg/.jpeg/.png/.webp/.mp4.
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple, Union

import requests
import yt_dlp

logger = logging.getLogger(__name__)

# Solo questi estensioni finiscono in assets (escludi .json/.txt di gallery-dl)
ASSETS_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".mp4"}


def _ig_ytdlp_cookies() -> dict:
    """Opzioni yt-dlp per l'auth Instagram.

    Priorità: file cookies.txt (INSTAGRAM_COOKIES_FILE, per il cloud dove non
    c'è un browser) → altrimenti cookie dal browser (INSTAGRAM_BROWSER, default
    chrome). Senza cookie IG risponde "empty media response" / 403.
    """
    cookies_file = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip()
    if cookies_file and Path(cookies_file).exists():
        return {"cookiefile": cookies_file}
    browser = os.getenv("INSTAGRAM_BROWSER", "chrome").strip().lower()
    return {"cookiesfrombrowser": (browser,)} if browser else {}


def _gallery_dl_cookie_args() -> List[str]:
    """Argomenti CLI di gallery-dl per l'auth Instagram (stessa logica di yt-dlp)."""
    cookies_file = os.getenv("INSTAGRAM_COOKIES_FILE", "").strip()
    if cookies_file and Path(cookies_file).exists():
        return ["--cookies", cookies_file]
    browser = os.getenv("INSTAGRAM_BROWSER", "chrome").strip().lower()
    return ["--cookies-from-browser", browser] if browser else []


def _youtube_ytdlp_opts() -> dict:
    """Supporto challenge YouTube moderno tramite Node + yt-dlp EJS."""
    opts = {
        "js_runtimes": {"node": {"path": None}},
        "remote_components": ["ejs:github"],
    }
    cookies_file = os.getenv("YOUTUBE_COOKIES_FILE", "").strip()
    if cookies_file and Path(cookies_file).exists():
        # I secret montati da Cloud Run sono read-only, mentre yt-dlp aggiorna
        # il cookie jar alla chiusura. Usa quindi una copia privata scrivibile.
        source = Path(cookies_file)
        if str(source).startswith("/secrets/"):
            writable_copy = Path("/tmp/kn5-youtube-cookies.txt")
            if not writable_copy.exists():
                shutil.copyfile(source, writable_copy)
                writable_copy.chmod(0o600)
            cookies_file = str(writable_copy)
        opts["cookiefile"] = cookies_file
    return opts


class _QuietYDLLogger:
    """Logger per yt-dlp che inghiotte i messaggi attesi.

    Sul rilevamento-tipo dei caroselli IG, yt-dlp emette "No video formats
    found!" per ogni slide-immagine: è atteso (le immagini le prende gallery-dl)
    e altrimenti spammerebbe i log del bot. Gli errori finiscono a livello debug.
    """

    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass

    def error(self, msg):
        logger.warning("yt-dlp: %s", msg)


class MediaFetcherError(Exception):
    """Eccezione base per errori del MediaFetcher."""

    pass


class InvalidURLError(MediaFetcherError):
    """URL non valido o non supportato."""

    pass


class VideoRemovedError(MediaFetcherError):
    """Video rimosso o non disponibile."""

    pass


class DownloadError(MediaFetcherError):
    """Errore durante il download."""

    pass


class MediaFetcher:
    """
    Scarica video da Instagram, estrae audio in MP3 e gestisce il cache.

    Richiede ffmpeg installato per l'estrazione audio.
    """

    def __init__(
        self,
        temp_dir: Union[str, Path] = "temp",
        cache_file: Union[str, Path] = "temp/download_cache.json",
    ):
        self.temp_dir = Path(temp_dir)
        self.cache_file = Path(cache_file)
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict = {}
        self._meta_cache: dict = {}  # in-memory cache per extract_metadata
        self._load_cache()

    # =================================================================
    # Metadata extraction (caption, hashtag, view_count, ...)
    # =================================================================

    def extract_metadata(self, url: str) -> dict:
        """
        Estrae metadati ricchi (caption, hashtag, autore, date, conteggi) senza
        scaricare media. Sia per Instagram sia per YouTube usa yt-dlp
        extract_info(download=False) — per IG con process=False per non
        risolvere i formati di ogni slide di un carosello (lento).

        Restituisce sempre un dict, anche se incompleto:
        {
            "url": str,
            "source": "instagram" | "youtube" | "unknown",
            "shortcode": str | None,
            "profile": str | None,        # username/handle dell'autore
            "title": str | None,
            "caption": str | None,        # testo letterale
            "hashtags": list[str],
            "mentions": list[str],
            "date": str | None,           # ISO YYYY-MM-DD
            "like_count": int | None,
            "view_count": int | None,
            "comment_count": int | None,
            "duration_sec": int | None,
        }
        """
        if url in self._meta_cache:
            return self._meta_cache[url]

        meta = {
            "url": url,
            "source": "unknown",
            "shortcode": None,
            "profile": None,
            "title": None,
            "caption": None,
            "hashtags": [],
            "mentions": [],
            "date": None,
            "like_count": None,
            "view_count": None,
            "comment_count": None,
            "duration_sec": None,
        }

        url_lower = url.lower()
        if "instagram.com" in url_lower or "instagr.am" in url_lower:
            meta.update(self._extract_ig_metadata(url))
        elif "youtube.com" in url_lower or "youtu.be" in url_lower:
            meta.update(self._extract_yt_metadata(url))

        self._meta_cache[url] = meta
        return meta

    def _extract_ig_metadata(self, url: str) -> dict:
        """Estrae caption/autore/data da Instagram via yt-dlp.

        instaloader non è più utilizzabile (interroga un doc_id GraphQL che IG
        restituisce null). yt-dlp coi cookie funziona: espone description
        (=caption), channel (=username), timestamp. hashtag e mention si
        ricavano dalla caption. process=False evita di risolvere i formati di
        tutte le slide di un carosello (lento/timeout).
        """
        out: dict = {"source": "instagram"}
        out["shortcode"] = self._shortcode_from_url(url)
        try:
            opts = {"quiet": True, "no_warnings": True, "socket_timeout": 30,
                    "retries": 2, **_ig_ytdlp_cookies()}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
        except Exception as e:
            logger.warning("Estrazione metadata IG fallita per %s: %s", url[:60], e)
            return out
        if not info:
            return out

        caption = info.get("description") or ""
        out.update({
            "profile": info.get("channel") or info.get("uploader") or info.get("uploader_id"),
            "caption": caption,
            "hashtags": sorted(set(re.findall(r"#(\w+)", caption))),
            "mentions": sorted(set(re.findall(r"@(\w[\w.]*)", caption))),
            "date": self._date_from_timestamp(info.get("timestamp")),
            "like_count": info.get("like_count"),
            "view_count": info.get("view_count"),
            "comment_count": info.get("comment_count"),
            "duration_sec": info.get("duration"),
        })
        # Titolo: prima riga della caption (max 80 char); fallback al title yt-dlp
        if caption:
            first_line = caption.strip().split("\n", 1)[0].strip()
            out["title"] = first_line[:80] if first_line else None
        elif info.get("title"):
            out["title"] = info["title"][:80]
        return out

    @staticmethod
    def _date_from_timestamp(ts) -> Optional[str]:
        """Unix timestamp → 'YYYY-MM-DD' (UTC), o None."""
        if not ts:
            return None
        try:
            from datetime import datetime, timezone
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OSError, OverflowError):
            return None

    def _extract_yt_metadata(self, url: str) -> dict:
        """Usa yt-dlp extract_info per il titolo e i metadati base."""
        out: dict = {"source": "youtube"}
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, **_youtube_ytdlp_opts()}) as ydl:
                info = ydl.extract_info(url, download=False)
            if not info:
                return out
            out.update({
                "profile": info.get("uploader") or info.get("channel"),
                "title": info.get("title"),
                "caption": info.get("description"),
                "date": (info.get("upload_date") or "")[:10] or None,
                "like_count": info.get("like_count"),
                "view_count": info.get("view_count"),
                "comment_count": info.get("comment_count"),
                "duration_sec": info.get("duration"),
            })
            # Normalizza date YYYYMMDD -> YYYY-MM-DD
            d = out.get("date")
            if d and len(d) == 8 and d.isdigit():
                out["date"] = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        except Exception as e:
            logger.warning("Estrazione metadata YouTube fallita per %s: %s", url[:60], e)
        return out

    def _load_cache(self) -> None:
        """Carica l'indice dei download dalla cache."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, encoding="utf-8") as f:
                    self._cache = json.load(f)
                logger.debug("Cache caricata: %d voci", len(self._cache))
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Impossibile caricare cache: %s", e)
                self._cache = {}

    def _save_cache(self) -> None:
        """Salva l'indice dei download nella cache."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, indent=2)
        except OSError as e:
            logger.warning("Impossibile salvare cache: %s", e)

    def _url_hash(self, url: str) -> str:
        """Genera un hash stabile per l'URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _shortcode_from_url(self, url: str) -> Optional[str]:
        """Estrae lo shortcode da URL Instagram (es. /p/ABC123/ o /reel/ABC123/)."""
        if not url:
            return None
        m = re.search(r"/p/([A-Za-z0-9_-]+)", url) or re.search(r"/reel/([A-Za-z0-9_-]+)", url)
        return m.group(1) if m else None

    def _find_downloaded_video(self, key: str, base_dir: Optional[Path] = None) -> Optional[Path]:
        """Trova il file video scaricato (può avere estensioni diverse)."""
        video_extensions = {".mp4", ".webm", ".mkv", ".m4a", ".mov"}
        search_dir = base_dir or self.temp_dir
        for p in search_dir.glob(f"{key}.*"):
            if p.suffix.lower() in video_extensions:
                return p
        return None

    def _is_image_file(self, path: Union[str, Path]) -> bool:
        """True se l'estensione è immagine (.jpg, .webp, .png, ecc.): non usare ffmpeg per audio."""
        return Path(path).suffix.lower() in {".jpg", ".jpeg", ".webp", ".png", ".gif"}

    def _extract_media_type_and_entries(self, url: str) -> Tuple[str, List[dict]]:
        """Estrae metadati con yt-dlp. Nessun format video obbligatorio (supporta immagini)."""
        # I cookie Instagram vanno passati SOLO agli URL Instagram: iniettarli su
        # YouTube rompe l'estrazione (decrypt cookie Chrome fallisce) e con
        # ignoreerrors=True yt-dlp torna None in silenzio.
        is_instagram = "instagram.com" in url.lower() or "instagr.am" in url.lower()
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "ignoreerrors": True,
            "socket_timeout": 30,
            "retries": 2,
            "logger": _QuietYDLLogger(),
            **(_youtube_ytdlp_opts() if not is_instagram else {}),
            **(_ig_ytdlp_cookies() if is_instagram else {}),
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        if not info:
            raise DownloadError("Impossibile estrarre metadati dal URL")

        # Parsing caroselli: _type == 'playlist' → itera su entries
        if info.get("_type") == "playlist":
            entries = [e for e in (info.get("entries") or []) if e]
            if entries:
                logger.info("Carosello rilevato: %d elementi", len(entries))
                return "carousel", entries
            raise DownloadError("Playlist senza entries")

        # Singolo elemento (o entry unica)
        entries = info.get("entries")
        if entries is not None:
            entries = [e for e in entries if e]
        if not entries:
            entries = [info]
        if len(entries) > 1:
            logger.info("Carosello rilevato: %d elementi", len(entries))
            return "carousel", entries

        entry = entries[0]
        has_duration = entry.get("duration") is not None
        ext = (entry.get("ext") or "").lower()
        image_exts = ("jpg", "jpeg", "png", "webp", "gif")
        if ext in image_exts or (not has_duration and not entry.get("formats")):
            return "image", entries
        return "video", entries

    def _assets_only_media(self, paths: List[Path]) -> List[str]:
        """Filtra solo file media (.jpg/.jpeg/.png/.webp/.mp4), esclude .json/.txt."""
        return [
            str(p.resolve())
            for p in paths
            if p.suffix.lower() in ASSETS_EXTENSIONS
        ]

    def _fetch_with_gallery_dl(self, url: str, key: str) -> Optional[dict]:
        """
        Scarica post/carosello/immagine con gallery-dl (coi cookie IG).

        gallery-dl gestisce nativamente i caroselli misti immagini+video di
        Instagram, che yt-dlp non riesce a estrarre (le slide-immagine danno
        "No video formats found"). Restituisce {media_type, assets, audio_path}.
        media_type è "carousel" se >1 asset, altrimenti "image"/"video" a
        seconda dell'estensione dell'unico asset.
        """
        out_dir = self.temp_dir / key
        out_dir.mkdir(parents=True, exist_ok=True)
        # Invocato come modulo dello stesso interprete: non dipende dal PATH
        # (il bot gira con ./venv/bin/python senza attivare il venv).
        cmd = [
            sys.executable, "-m", "gallery_dl",
            *_gallery_dl_cookie_args(),
            "-D", str(out_dir),          # tutti i file in out_dir, senza sottocartelle
            "--no-mtime",
            url,
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError:
            logger.error("gallery-dl non installato (pip install gallery-dl)")
            return None
        except subprocess.TimeoutExpired:
            logger.warning("gallery-dl timeout su %s", url[:60])
            return None
        if proc.returncode != 0:
            logger.warning("gallery-dl exit %s su %s: %s", proc.returncode, url[:60],
                           (proc.stderr or "").strip()[:300])
            # returncode != 0 non è per forza fatale: alcuni file possono esser scesi

        media_files = sorted(
            (p for p in out_dir.iterdir()
             if p.is_file() and p.suffix.lower() in ASSETS_EXTENSIONS),
            key=lambda p: p.name,
        )
        asset_paths = self._assets_only_media(media_files)
        if not asset_paths:
            logger.warning("gallery-dl: nessun asset media in %s", out_dir)
            return None

        if len(asset_paths) > 1:
            media_type = "carousel"
        elif Path(asset_paths[0]).suffix.lower() == ".mp4":
            media_type = "video"
        else:
            media_type = "image"
        logger.info("gallery-dl: %d asset scaricati (%s)", len(asset_paths), media_type)
        return {"media_type": media_type, "assets": asset_paths, "audio_path": None}

    def _validate_instagram_url(self, url: str) -> None:
        """Valida che l'URL sia supportato (Instagram o YouTube)."""
        if not url or not isinstance(url, str):
            raise InvalidURLError("URL non valido o vuoto")
        url_lower = url.strip().lower()
        allowed = ["instagram.com", "instagr.am", "youtube.com", "youtu.be"]
        if not any(domain in url_lower for domain in allowed):
            raise InvalidURLError(
                f"URL non supportato (atteso Instagram o YouTube): {url[:80]}..."
            )

    def check_existing(self, url: str) -> Optional[dict]:
        """
        Verifica se il contenuto per questo URL è già stato scaricato.

        Returns:
            dict con media_type, assets, audio_path se tutti gli asset esistono, altrimenti None.
        """
        self._validate_instagram_url(url)
        key = self._url_hash(url)

        if key not in self._cache:
            logger.debug("URL non in cache: %s", url[:50])
            return None

        entry = self._cache[key]
        # Compatibilità con cache vecchia (video_path/audio_path)
        assets_list = entry.get("assets")
        if not assets_list and entry.get("video_path"):
            assets_list = [entry["video_path"]]
        for p in (assets_list or []):
            if not Path(p).exists():
                logger.info("Asset in cache non più presente: %s, rimuovo voce: %s", p, key)
                del self._cache[key]
                self._save_cache()
                return None
        ap = entry.get("audio_path")
        if ap and not Path(ap).exists():
            logger.info("Audio in cache non più presente, rimuovo voce: %s", key)
            del self._cache[key]
            self._save_cache()
            return None

        audio_resolved = str(Path(ap).resolve()) if ap else None
        resolved_assets = [str(Path(p).resolve()) for p in (assets_list or [])]

        logger.info("Contenuto già presente per URL: %s", url[:50])
        return {
            "media_type": entry.get("media_type", "video"),
            "assets": resolved_assets,
            "audio_path": audio_resolved,
        }

    def _extract_audio_mp3(self, video_path: Path, audio_path: Path) -> bool:
        """Estrae l'audio dal video in MP3. Restituisce True se ok, False se fallisce (es. video muto)."""
        logger.info("Estrazione audio in MP3: %s -> %s", video_path, audio_path)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-acodec",
                    "libmp3lame",
                    "-q:a",
                    "2",
                    str(audio_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except FileNotFoundError as err:
            logger.warning("ffmpeg non trovato: %s", err)
            return False
        except subprocess.CalledProcessError as err:
            logger.warning("Estrazione audio fallita (video muto?): %s", getattr(err, "stderr", err))
            return False

    def _fetch_youtube_with_apify(self, url: str, key: str) -> Optional[dict]:
        """Fallback cloud per YouTube quando l'IP di Cloud Run viene bloccato.

        L'Actor usa la propria rete di acquisizione e restituisce un MP4
        temporaneo; KN5 lo copia subito nel workspace del job e prosegue con
        lo stesso percorso audio+video usato per i download diretti.
        """
        token = os.getenv("APIFY_TOKEN", "").strip()
        if not token:
            return None

        try:
            from datetime import timedelta
            from decimal import Decimal
            from apify_client import ApifyClient

            logger.info("Download YouTube tramite fallback Apify: %s", url[:60])
            client = ApifyClient(token)
            run = client.actor("streamers/youtube-video-downloader").call(
                run_input={
                    "videos": [{"url": url}],
                    "storeInKVStore": True,
                    "preferredQuality": os.getenv("APIFY_YOUTUBE_QUALITY", "360p"),
                    "preferredFormat": "mp4",
                },
                max_total_charge_usd=Decimal(os.getenv("APIFY_YOUTUBE_MAX_CHARGE_USD", "0.50")),
                run_timeout=timedelta(minutes=20),
                logger=None,
            )
            status = getattr(run, "status", None) if run else None
            if status != "SUCCEEDED":
                logger.warning("Fallback Apify non completato: %s", status or "nessun run")
                return None

            dataset_id = getattr(run, "default_dataset_id", None)
            if not dataset_id:
                return None
            items = list(client.dataset(dataset_id).iterate_items())
            item = next((entry for entry in items if entry.get("downloadedFileUrl")), None)
            if not item:
                logger.warning("Fallback Apify senza URL di download")
                return None

            video_path = self.temp_dir / f"{key}.mp4"
            with requests.get(item["downloadedFileUrl"], stream=True, timeout=(30, 300)) as response:
                response.raise_for_status()
                with open(video_path, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            output.write(chunk)
            if not video_path.exists() or video_path.stat().st_size == 0:
                return None

            audio_path = self.temp_dir / f"{key}.mp3"
            audio_resolved = None
            if self._extract_audio_mp3(video_path, audio_path) and audio_path.exists():
                audio_resolved = str(audio_path.resolve())
            result = {
                "media_type": "video",
                "assets": [str(video_path.resolve())],
                "audio_path": audio_resolved,
                "acquisition_cost_usd": float(getattr(run, "usage_total_usd", 0) or 0),
            }
            self._cache[key] = {"url": url, **result}
            self._save_cache()
            return result
        except Exception as err:
            logger.warning("Fallback Apify YouTube fallito: %s", err, exc_info=True)
            return None

    def _handle_ytdlp_error(self, err: Exception, url: str) -> None:
        """Mappa errori yt-dlp in eccezioni chiare."""
        msg = str(err).lower()
        if "unable to extract" in msg or "video unavailable" in msg:
            raise VideoRemovedError(
                f"Video non disponibile o rimosso: {url[:80]}..."
            ) from err
        if "invalid" in msg or "unsupported" in msg or "no video" in msg:
            raise InvalidURLError(f"URL non valido o non supportato: {url[:80]}...") from err
        raise DownloadError(f"Errore download: {err}") from err

    def fetch(self, url: str) -> dict:
        """
        Scarica contenuto da Instagram (carousel, immagine o video), estrae audio se presente.

        Prima esegue check_existing; se il contenuto esiste, restituisce il dict in cache.

        Returns:
            dict con media_type ("carousel"|"image"|"video"), assets (lista path), audio_path (str o None).
        """
        self._validate_instagram_url(url)

        existing = self.check_existing(url)
        if existing:
            return existing

        key = self._url_hash(url)

        def gallery_dl_download() -> Optional[dict]:
            """Scarica con gallery-dl e aggiorna la cache."""
            result = self._fetch_with_gallery_dl(url, key)
            if result:
                self._cache[key] = {"url": url, **result}
                self._save_cache()
            return result

        is_instagram = "instagram.com" in url.lower() or "instagr.am" in url.lower()

        # Determina il tipo con yt-dlp; se l'estrazione fallisce, per Instagram
        # gallery-dl fa da fallback totale (gestisce anche i post statici).
        try:
            media_type, entries = self._extract_media_type_and_entries(url)
        except (yt_dlp.utils.DownloadError, InvalidURLError) as err:
            if is_instagram:
                fb = gallery_dl_download()
                if fb:
                    logger.info("yt-dlp non ha estratto il tipo, usato gallery-dl per %s", url[:60])
                    return fb
            else:
                fb = self._fetch_youtube_with_apify(url, key)
                if fb:
                    return fb
            self._handle_ytdlp_error(err, url)
        except Exception as err:
            if is_instagram:
                fb = gallery_dl_download()
                if fb:
                    return fb
            else:
                fb = self._fetch_youtube_with_apify(url, key)
                if fb:
                    return fb
            raise DownloadError(f"Errore estrazione metadati: {err}") from err

        logger.info("Tipo rilevato: %s, download da Instagram: %s", media_type, url[:60])

        # Caroselli e immagini Instagram: gallery-dl (yt-dlp non estrae le
        # slide-immagine di IG, danno "No video formats found").
        if is_instagram and media_type in ("carousel", "image"):
            result = gallery_dl_download()
            if result:
                return result
            raise DownloadError(f"{media_type}: nessun asset scaricato (gallery-dl)")

        # video / reel (nessun format obbligatorio tipo bestvideo per non fallire su edge case)
        ydl_opts = {
            "outtmpl": str(self.temp_dir / f"{key}.%(ext)s"),
            "quiet": False,
            "no_warnings": False,
            "format": "best[ext=mp4]/best/best",
            "ignoreerrors": True,
            "socket_timeout": 30,
            "retries": 2,
            **(_youtube_ytdlp_opts() if not is_instagram else {}),
            **(_ig_ytdlp_cookies() if is_instagram else {}),
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except yt_dlp.utils.DownloadError as err:
            if is_instagram:
                fb = gallery_dl_download()
                if fb:
                    logger.info("yt-dlp video fallito, usato gallery-dl per %s", url[:60])
                    return fb
            else:
                fb = self._fetch_youtube_with_apify(url, key)
                if fb:
                    return fb
            self._handle_ytdlp_error(err, url)
        except Exception as err:
            if not is_instagram:
                fb = self._fetch_youtube_with_apify(url, key)
                if fb:
                    return fb
            raise DownloadError(f"Errore inatteso durante download: {err}") from err

        video_path = self._find_downloaded_video(key)
        if not video_path:
            if is_instagram:
                fb = gallery_dl_download()
                if fb:
                    return fb
            else:
                fb = self._fetch_youtube_with_apify(url, key)
                if fb:
                    return fb
            raise DownloadError("Download completato ma file video non trovato")
        logger.info("Video scaricato: %s", video_path)

        # Audio condizionale: solo per file video; .jpg/.webp/.png → audio_path = None, niente ffmpeg
        audio_resolved = None
        if not self._is_image_file(video_path):
            audio_path = self.temp_dir / f"{key}.mp3"
            if self._extract_audio_mp3(video_path, audio_path) and audio_path.exists():
                audio_resolved = str(audio_path.resolve())
            else:
                logger.info("Audio non disponibile o estrazione fallita, proseguo senza audio")

        assets = [str(video_path.resolve())]
        self._cache[key] = {
            "url": url,
            "media_type": "video",
            "assets": assets,
            "audio_path": audio_resolved,
        }
        self._save_cache()
        return {"media_type": "video", "assets": assets, "audio_path": audio_resolved}
