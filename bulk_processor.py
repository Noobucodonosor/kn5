#!/usr/bin/env python3
"""
bulk_processor: scarica e processa in batch tutti i contenuti di un canale
YouTube o profilo Instagram, salvandoli in library/profiles/<nome>/.

Flusso:
1. Estrae elenco URL (yt-dlp per YouTube, instaloader per Instagram).
2. Stima costo totale Gemini e confronta con budget mensile residuo.
3. Chiede conferma.
4. Per ogni URL: download → metadata → Gemini → budget.record → library write.
5. Si ferma automaticamente se nel mezzo si supera la soglia.
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yt_dlp
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("bulk_processor")

try:
    import instaloader
except ImportError:
    instaloader = None

try:
    from apify_client import ApifyClient
except ImportError:
    ApifyClient = None

from src.skills.budget import BudgetExceeded, BudgetTracker
from src.skills.knowledge_synthesizer import KnowledgeSynthesizer
from src.skills.library_writer import LibraryWriter
from src.skills.media_fetcher import MediaFetcher

# Stima token per la previsione costi (prima di eseguire)
TOKENS_PER_VIDEO_SECOND = 260
TOKENS_PER_CAROUSEL = 15000
AVERAGE_OUTPUT_TOKENS = 2500


def parse_args():
    parser = argparse.ArgumentParser(
        description="kn5 bulk: estrai tutti i pezzi di un canale YouTube o profilo Instagram."
    )
    parser.add_argument(
        "target",
        help="URL canale YouTube (es. https://www.youtube.com/@nome) o username Instagram (es. nome_profilo)",
    )
    parser.add_argument(
        "-l", "--limit", type=int, default=0,
        help="Limite massimo di contenuti da processare (0 = nessun limite).",
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Salta la conferma interattiva (per cron / script).",
    )
    parser.add_argument(
        "--from-urls", default=None, metavar="FILE",
        help="File con URL Instagram (uno per riga). Salta scansione profilo, "
             "usa direttamente la lista.",
    )
    return parser.parse_args()


def read_urls_from_file(path: str) -> List[Dict]:
    """Carica URL da file (uno per riga, # = commento). Output formato bulk-friendly."""
    p = Path(path)
    if not p.exists():
        logger.error("File URL non trovato: %s", path)
        return []
    items: List[Dict] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # Estrai URL anche se la riga ha altro testo
        m = re.search(r"https?://[^\s]+", line)
        url = m.group(0) if m else line
        # Inferisci shortcode (utile per title placeholder)
        sc_m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
        sc = sc_m.group(1) if sc_m else None
        items.append({
            "url": url,
            "title": f"Post {sc}" if sc else url[:60],
            "duration": 30,
            "media_type": "video",  # placeholder, raffinato da fetch()
        })
    logger.info("Caricati %d URL da %s", len(items), path)
    return items


# =====================================================================
# Scoperta URL
# =====================================================================


def extract_youtube_urls(channel_url: str, limit: int = 0) -> List[Dict]:
    logger.info("Scansione canale YouTube: %s", channel_url)
    ydl_opts = {
        "extract_flat": True,
        "quiet": True,
        "playlistend": limit if limit > 0 else None,
    }
    items: List[Dict] = []
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            playlist = ydl.extract_info(channel_url, download=False)
            if not playlist:
                return []
            for entry in (playlist.get("entries") or []):
                if not entry:
                    continue
                v_id = entry.get("id")
                url = entry.get("url") or (f"https://www.youtube.com/watch?v={v_id}" if v_id else None)
                if not url:
                    continue
                items.append({
                    "url": url,
                    "title": entry.get("title", "Video"),
                    "duration": entry.get("duration") or 0,
                    "media_type": "video",
                })
    except Exception as e:
        logger.error("Errore estrazione URL YouTube: %s", e)
    return items


def _username_from_target(target: str) -> str:
    """Estrae username Instagram da URL o stringa raw."""
    s = target.strip()
    if "instagram.com" in s or "instagr.am" in s:
        m = re.search(r"instagram\.com/([a-zA-Z0-9_\.]+)", s)
        if m:
            return m.group(1)
    return s.lstrip("@")


def _run_ytdlp_subprocess(profile_url: str, browser: str, limit: int, timeout_sec: int = 45) -> Optional[dict]:
    """
    Lancia yt-dlp come subprocess con timeout vero. Necessario perché
    --cookies-from-browser=chrome su macOS può bloccare in attesa del
    Keychain se invocato da un processo non interattivo, e l'opzione di
    timeout di yt-dlp non copre quella fase.
    """
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "--flat-playlist",
        "--dump-single-json",
        "--no-warnings",
        "--quiet",
        "--cookies-from-browser", browser,
        "--socket-timeout", "30",
    ]
    if limit > 0:
        cmd.extend(["--playlist-end", str(limit)])
    cmd.append(profile_url)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp browser=%s: timeout dopo %ds (probabile prompt Keychain)", browser, timeout_sec)
        return None
    if result.returncode != 0:
        stderr_tail = (result.stderr or "").strip().splitlines()[-3:]
        logger.debug("yt-dlp browser=%s: exit=%d stderr=%s",
                     browser, result.returncode, " | ".join(stderr_tail)[:200])
        return None
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return None


def extract_instagram_urls_via_browser(username: str, limit: int = 0) -> List[Dict]:
    """
    Tenta di scoprire i post di un profilo IG usando i cookies del browser
    dove l'utente è già loggato. Più affidabile di un login programmatico
    perché evita il checkpoint anti-bot di Instagram.

    Ordine browser:
    - Se INSTAGRAM_BROWSER nel .env è settato → solo quello.
    - Altrimenti: firefox → safari → chrome → edge → brave.
      (Firefox per primo perché su macOS non richiede Keychain.)

    Ogni tentativo ha un timeout duro di 45s — niente hang silenziosi.
    """
    profile_url = f"https://www.instagram.com/{username}/"

    requested = os.getenv("INSTAGRAM_BROWSER", "").strip().lower()
    browsers_to_try = (
        [requested] if requested
        else ["firefox", "safari", "chrome", "edge", "brave"]
    )
    browsers_to_try = [b for b in browsers_to_try if b]

    for browser in browsers_to_try:
        logger.info("Tentativo scoperta profilo via cookies %s…", browser)
        info = _run_ytdlp_subprocess(profile_url, browser, limit)
        if not info or not info.get("entries"):
            logger.info("Browser %s: nessun risultato", browser)
            continue

        items: List[Dict] = []
        for entry in info["entries"]:
            if not entry:
                continue
            shortcode = entry.get("id") or entry.get("display_id")
            entry_url = entry.get("url") or (
                f"https://www.instagram.com/p/{shortcode}/" if shortcode else None
            )
            if not entry_url:
                continue
            title = entry.get("title") or (entry.get("description") or "")[:80] or f"Post {shortcode or ''}"
            items.append({
                "url": entry_url,
                "title": title,
                "duration": entry.get("duration") or 30,
                "media_type": "video",
            })

        if items:
            logger.info("✅ Scansione via cookies %s: %d post trovati", browser, len(items))
            return items

    return []


def extract_instagram_urls_via_apify(username: str, limit: int = 0) -> List[Dict]:
    """
    Usa Apify (apify/instagram-profile-scraper) per ottenere la lista post del
    profilo. Affidabile e legale, non dipende da auth IG.

    Richiede APIFY_TOKEN in .env. Costo tipico: ~$2 per 1000 risultati.
    Actor override-abile via APIFY_INSTAGRAM_ACTOR (default: apify/instagram-profile-scraper).
    """
    token = os.getenv("APIFY_TOKEN")
    if not token:
        return []
    if ApifyClient is None:
        logger.warning("APIFY_TOKEN settato ma apify-client non installato")
        return []

    actor_id = os.getenv("APIFY_INSTAGRAM_ACTOR", "apify/instagram-scraper")
    results_limit = limit if limit > 0 else 100

    client = ApifyClient(token)

    # apify/instagram-scraper input format
    run_input = {
        "directUrls": [f"https://www.instagram.com/{username}/"],
        "resultsType": "posts",
        "resultsLimit": results_limit,
        "searchType": "user",
        "addParentData": False,
    }

    logger.info("Apify: avvio attore %s per @%s (limit=%d)…", actor_id, username, results_limit)
    try:
        run = client.actor(actor_id).call(run_input=run_input)
    except Exception as e:
        logger.error("Apify call fallita: %s", e)
        return []

    if not run:
        logger.error("Apify: run vuoto")
        return []

    # apify-client v2+ restituisce un oggetto Pydantic invece di un dict
    if hasattr(run, "model_dump"):
        run_data = run.model_dump()
    elif isinstance(run, dict):
        run_data = run
    else:
        run_data = dict(run)

    dataset_id = run_data.get("defaultDatasetId") or run_data.get("default_dataset_id")
    status = run_data.get("status")
    if status and status not in ("SUCCEEDED", "RUNNING"):
        logger.error("Apify: run terminato con status=%s, chiavi=%s", status, list(run_data.keys()))
        return []
    if not dataset_id:
        logger.error("Apify: dataset_id non trovato. Run keys=%s, status=%s", list(run_data.keys()), status)
        return []

    items: List[Dict] = []
    try:
        for entry in client.dataset(dataset_id).iterate_items():
            # apify/instagram-profile-scraper restituisce post con questi campi:
            #   url, shortCode, type (Image|Video|Sidecar), caption,
            #   timestamp (ISO), videoDuration, ownerUsername,
            #   likesCount, videoViewCount, commentsCount
            url = entry.get("url") or (
                f"https://www.instagram.com/p/{entry.get('shortCode')}/"
                if entry.get("shortCode") else None
            )
            if not url:
                continue

            ig_type = (entry.get("type") or "").lower()
            if ig_type == "video":
                media_type = "video"
            elif ig_type == "sidecar":
                media_type = "carousel"
            else:
                media_type = "image"

            duration = int(entry.get("videoDuration") or 0)
            if media_type == "video" and duration == 0:
                duration = 30  # stima conservativa per la previsione costi

            caption = (entry.get("caption") or "").strip()
            title = caption.split("\n", 1)[0][:80] if caption else f"Post {entry.get('shortCode', '')}"

            items.append({
                "url": url,
                "title": title,
                "duration": duration,
                "media_type": media_type,
            })
    except Exception as e:
        logger.error("Apify: errore lettura dataset: %s", e)
        return items  # restituisce quel che ha raccolto fin qui

    logger.info("✅ Apify: %d post recuperati per @%s", len(items), username)
    return items


def extract_instagram_urls(target: str, limit: int = 0) -> List[Dict]:
    username = _username_from_target(target)

    # 1° tentativo: Apify (più affidabile in assoluto)
    if os.getenv("APIFY_TOKEN"):
        items = extract_instagram_urls_via_apify(username, limit)
        if items:
            return items
        logger.info("Apify non ha prodotto risultati, provo fallback")

    # 2° tentativo: yt-dlp con cookies dal browser
    items = extract_instagram_urls_via_browser(username, limit)
    if items:
        return items

    logger.info("Cookies browser non disponibili o vuoti, tento instaloader")

    if instaloader is None:
        logger.error("instaloader non installato e cookies browser non disponibili")
        return []

    logger.info("Scansione profilo Instagram via instaloader: @%s", username)
    L = instaloader.Instaloader(download_pictures=False, download_videos=False,
                                save_metadata=False, download_comments=False)
    insta_user = os.getenv("INSTAGRAM_USER")
    insta_password = os.getenv("INSTAGRAM_PASSWORD")
    if insta_user and insta_password:
        try:
            L.login(insta_user, insta_password)
        except Exception as e:
            logger.warning("Login IG fallito, procedo anonimo: %s", e)

    posts: List[Dict] = []
    try:
        profile = instaloader.Profile.from_username(L.context, username)
        for count, post in enumerate(profile.get_posts()):
            if limit > 0 and count >= limit:
                break
            media_type = "video" if post.is_video else "carousel"
            posts.append({
                "url": f"https://www.instagram.com/p/{post.shortcode}/",
                "title": f"Post {post.date_utc.strftime('%Y-%m-%d %H:%M')}",
                "duration": 30 if post.is_video else 0,
                "media_type": media_type,
            })
    except Exception as e:
        logger.error("Errore scansione profilo: %s", e)
    return posts


# =====================================================================
# Stima costi
# =====================================================================


def estimate_costs(items: List[Dict], budget: BudgetTracker) -> Tuple[float, int, int]:
    """Stima costo totale + tokens. Usa il pricing del BudgetTracker per coerenza."""
    total_in = 0
    total_out = 0
    for item in items:
        if item["media_type"] == "video" and item.get("duration", 0) > 0:
            total_in += int(item["duration"] * TOKENS_PER_VIDEO_SECOND)
        else:
            total_in += TOKENS_PER_CAROUSEL
        total_out += AVERAGE_OUTPUT_TOKENS
    cost = budget.estimate_cost(total_in, total_out)
    return cost, total_in, total_out


# =====================================================================
# Main
# =====================================================================


def main():
    load_dotenv()
    args = parse_args()
    target = args.target

    # Tipo target / sorgente lista
    is_youtube = "youtube.com" in target or "youtu.be" in target

    if args.from_urls:
        items = read_urls_from_file(args.from_urls)
        profile_name = re.sub(r"[^\w\-]", "_", _username_from_target(target)) if not is_youtube else "youtube"
    elif is_youtube:
        items = extract_youtube_urls(target, args.limit)
        profile_name = re.sub(r"[^\w\-]", "_", target.split("/")[-1] or "youtube")
    else:
        items = extract_instagram_urls(target, args.limit)
        profile_name = re.sub(r"[^\w\-]", "_", target.split("/")[-1] or "instagram")

    if not items:
        logger.error("Nessun contenuto trovato. Esco.")
        sys.exit(1)

    budget = BudgetTracker()
    cost_est, total_in, total_out = estimate_costs(items, budget)
    st = budget.status()

    print("\n" + "=" * 56)
    print("REPORT STIMA COSTI — kn5 bulk")
    print("=" * 56)
    print(f"Contenuti: {len(items)}")
    print(f"Token input stimati: {total_in:,}")
    print(f"Token output stimati: {total_out:,}")
    print(f"Costo stimato: ${cost_est:.4f}")
    print(f"Budget mese {st['month']}: ${st['spent_usd']:.2f}/${st['limit_usd']:.2f} "
          f"({st['percent']}%)")
    print(f"Rimanente: ${st['remaining_usd']:.2f}")
    if cost_est > st["remaining_usd"]:
        print("⚠️  La stima eccede il budget rimanente — il bulk si fermerà prima della fine.")
    print("=" * 56 + "\n")

    if not args.yes:
        try:
            conferma = input("Procedere? (s/n): ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nAnnullato.")
            sys.exit(0)
        if conferma != "s":
            print("Annullato.")
            sys.exit(0)

    # Setup directory temp + componenti
    temp_dir = Path("temp/bulk_temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    synthesizer = KnowledgeSynthesizer()
    writer = LibraryWriter()

    success = 0
    failed = 0
    skipped = 0
    stopped_budget = False

    # Mappa shortcode → file esistente, per skip dei pezzi già processati.
    # Scandisce TUTTE le sottocartelle in library/profiles + inbox: un pezzo
    # può essere finito ovunque a seconda del profilo dedotto dai metadati IG,
    # che non coincide sempre con il target del bulk.
    existing_shortcodes = set()
    for root in [Path("library/profiles"), Path("library/inbox")]:
        if not root.exists():
            continue
        for f in root.rglob("*.md"):
            if f.name == "index.md":
                continue
            m = re.match(r"\d{4}-\d{2}-\d{2}_([A-Za-z0-9_-]+)_", f.name)
            if m:
                existing_shortcodes.add(m.group(1))
    if existing_shortcodes:
        logger.info("Trovati %d pezzi già in biblioteca, verranno skippati", len(existing_shortcodes))

    for i, item in enumerate(items):
        url = item["url"]
        title = item["title"]

        # Skip se già processato
        sc_m = re.search(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)", url)
        if sc_m and sc_m.group(1) in existing_shortcodes:
            logger.info("[%d/%d] ⊘ skip (già in biblioteca): %s", i + 1, len(items), sc_m.group(1))
            skipped += 1
            continue

        # Budget gate prima di ogni pezzo (skippati non contano)
        try:
            budget.check_or_raise()
        except BudgetExceeded as e:
            logger.error("Stop: %s", e)
            stopped_budget = True
            break

        logger.info("[%d/%d] %s — %s", i + 1, len(items), title, url[:60])

        session_dir = temp_dir / f"item_{i}"
        session_dir.mkdir(parents=True, exist_ok=True)
        fetcher = MediaFetcher(temp_dir=session_dir, cache_file=temp_dir / "download_cache.json")

        try:
            result = fetcher.fetch(url)
            media_type = result.get("media_type", "video")
            assets = result["assets"]
            metadata = fetcher.extract_metadata(url)
            metadata["media_type"] = media_type

            if media_type == "video":
                pm = synthesizer.generate_from_video(assets[0])
            else:
                pm = synthesizer.generate_from_images(assets)

            usage = pm.pop("_usage", {"input_tokens": 0, "output_tokens": 0})
            label = pm.get("title") or title or url[:60]
            cost = budget.record(
                input_tokens=usage["input_tokens"],
                output_tokens=usage["output_tokens"],
                model=synthesizer.model_name,
                label=label,
            )

            md_path = writer.write_piece(pm, metadata, cost_usd=cost)
            logger.info("✅ %s  ($%.4f)", md_path.name, cost)
            success += 1

        except Exception as e:
            logger.error("❌ Errore su %s: %s", url[:60], e)
            failed += 1

        finally:
            try:
                if session_dir.exists():
                    shutil.rmtree(session_dir)
            except Exception as e:
                logger.warning("Cleanup %s fallito: %s", session_dir, e)

    # Cleanup temp finale
    try:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
    except Exception:
        pass

    final = budget.status()
    print("\n" + "=" * 56)
    print("BULK COMPLETATO" if not stopped_budget else "BULK INTERROTTO (budget)")
    print("=" * 56)
    print(f"Successi: {success}/{len(items)}")
    print(f"Fallimenti: {failed}")
    print(f"Skippati (già in biblioteca): {skipped}")
    if stopped_budget:
        print(f"Pezzi non processati: {len(items) - success - failed}")
    print(f"Profilo target: {profile_name}")
    print(f"Spesa reale stimata sul bulk: ${final['spent_usd'] - st['spent_usd']:.4f}")
    print(f"Budget post-run: ${final['spent_usd']:.2f}/${final['limit_usd']:.2f} ({final['percent']}%)")
    print("=" * 56 + "\n")


if __name__ == "__main__":
    main()
