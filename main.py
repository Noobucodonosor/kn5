#!/usr/bin/env python3
"""
Orchestratore CLI di kn5.

Pipeline: URL → MediaFetcher (download) → KnowledgeSynthesizer (Gemini) →
LibraryWriter (markdown in library/). BudgetTracker fa da sentinella prima
di ogni chiamata Gemini.

HTML report opzionale via --html.
"""

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

from src.skills.budget import BudgetExceeded, BudgetTracker
from src.skills.html_generator import generate_epistemic_html
from src.skills.knowledge_synthesizer import (
    KnowledgeSynthesizer,
    KnowledgeSynthesizerError,
)
from src.skills.library_writer import LibraryWriter
from src.skills.media_fetcher import (
    DownloadError,
    InvalidURLError,
    MediaFetcher,
    VideoRemovedError,
)

TEMP_VIDEO = Path("temp/video")
OUTPUT_DIR = Path("output")


def ensure_dirs():
    TEMP_VIDEO.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def get_url(args_url: Optional[str]) -> Optional[str]:
    if args_url:
        return args_url.strip()
    return (os.environ.get("REEL_URL") or "").strip() or None


def process_url(url: str, write_html: bool = False) -> Path:
    """
    Pipeline completa per un singolo URL.

    Returns:
        Path del file markdown scritto in library/.
    """
    ensure_dirs()
    fetcher = MediaFetcher(
        temp_dir=TEMP_VIDEO / MediaFetcher()._url_hash(url),
        cache_file=TEMP_VIDEO / "download_cache.json",
    )
    session_dir = fetcher.temp_dir

    # Sentinella budget PRIMA di toccare Gemini
    budget = BudgetTracker()
    try:
        budget.check_or_raise()
    except BudgetExceeded as e:
        print(f"\n❌ {e}\n", file=sys.stderr)
        sys.exit(2)

    st = budget.status()
    print(
        f"💰 Budget {st['month']}: ${st['spent_usd']:.2f}/${st['limit_usd']:.2f} "
        f"({st['percent']}%)"
    )

    # 1. Download
    print(f"⬇️  Download: {url[:80]}")
    try:
        result = fetcher.fetch(url)
    except InvalidURLError as e:
        print(f"URL non valido: {e}", file=sys.stderr)
        sys.exit(1)
    except VideoRemovedError as e:
        print(f"Video non disponibile: {e}", file=sys.stderr)
        sys.exit(1)
    except DownloadError as e:
        print(f"Errore download: {e}", file=sys.stderr)
        sys.exit(1)

    media_type = result.get("media_type", "video")
    assets = result["assets"]
    print(f"✅ {media_type} ({len(assets)} asset)")

    # 2. Metadata (caption, hashtag, profilo, ...)
    print("📎 Estrazione metadata...")
    metadata = fetcher.extract_metadata(url)
    metadata["media_type"] = media_type
    if metadata.get("profile"):
        print(f"👤 Profilo: {metadata['profile']}")

    # 3. Gemini
    try:
        synthesizer = KnowledgeSynthesizer()
    except KnowledgeSynthesizerError as e:
        print(f"Errore config Vertex AI: {e}", file=sys.stderr)
        sys.exit(1)

    print("🧠 Analisi Gemini in corso...")
    try:
        if media_type == "video":
            pm = synthesizer.generate_from_video(assets[0])
        else:
            pm = synthesizer.generate_from_images(assets)
    except Exception as e:
        print(f"Errore Gemini: {e}", file=sys.stderr)
        sys.exit(1)

    # 4. Budget record
    usage = pm.pop("_usage", {"input_tokens": 0, "output_tokens": 0})
    label = metadata.get("title") or metadata.get("shortcode") or url[:60]
    cost = budget.record(
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
        model=synthesizer.model_name,
        label=label,
    )
    print(f"💸 Costo stimato chiamata: ${cost:.4f}")

    # 5. Library write
    writer = LibraryWriter()
    md_path = writer.write_piece(pm, metadata, cost_usd=cost)
    print(f"📚 Salvato in: {md_path}")

    # 6. HTML opzionale
    if write_html:
        html_path = _write_html_report(pm, metadata, assets, media_type)
        print(f"🌐 HTML: {html_path}")

    # 7. Cleanup sessione locale
    try:
        if session_dir.exists():
            shutil.rmtree(session_dir)
    except Exception as e:
        logger.warning("Cleanup sessione fallito: %s", e)

    return md_path


def _write_html_report(pm: dict, metadata: dict, assets: list, media_type: str) -> Path:
    """Genera il vecchio report HTML come output opzionale."""
    selected_frames = [] if media_type == "video" else assets[:6]
    html = generate_epistemic_html(
        title=pm.get("title", "Mappa di Processo"),
        executive_summary=pm.get("executive_summary", ""),
        steps=pm.get("steps", []),
        epistemic_insights=pm.get("epistemic_insights", []),
        image_paths=selected_frames,
    )
    title = pm.get("title", "report")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:40]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    shortcode = metadata.get("shortcode", "")
    name = f"processo_{safe}_{shortcode}_{ts}.html" if shortcode else f"processo_{safe}_{ts}.html"
    out_path = OUTPUT_DIR / name
    out_path.write_text(html, encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="kn5 CLI: scarica un Reel/Carosello Instagram (o video YouTube), "
        "analizza con Gemini, salva in library/ come markdown."
    )
    parser.add_argument(
        "url", nargs="?", default=None,
        help="URL del contenuto (oppure variabile d'ambiente REEL_URL)"
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Genera anche il vecchio report HTML in output/"
    )
    args = parser.parse_args()

    url = get_url(args.url)
    if not url:
        print("Errore: fornisci un URL (argomento o variabile REEL_URL).", file=sys.stderr)
        sys.exit(1)

    process_url(url, write_html=args.html)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
