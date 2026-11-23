#!/usr/bin/env python3
"""
Orchestratore CLI di kn5.

Pipeline: URL → MediaFetcher (download) → KnowledgeSynthesizer (Gemini) →
LibraryWriter (markdown in library/). BudgetTracker fa da sentinella prima
di ogni chiamata Gemini.

HTML report opzionale via --html.
"""

import argparse
import json
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


def export_demo_samples(out_path: Path = Path("samples.json")) -> Path:
    """Esporta 3 pacchetti dimostrativi reali e sanitizzati per la preview statica (TASK-KN5-01)."""
    samples = [
        {
            "id": "youtube_newton_leibniz_tutorial",
            "title": "Formula di Newton–Leibniz",
            "full_title": "Formula di Newton–Leibniz per il calcolo degli integrali definiti",
            "eyebrow": "Matematica · 26 aprile 2026",
            "meta": "YouTube · Tonino Filardi",
            "author": "Tonino Filardi",
            "dur": "08:34",
            "duration_label": "8 min",
            "language": "Italiano",
            "src": "youtube",
            "long": False,
            "content_kind": "tutorial",
            "source_url": "https://www.youtube.com/watch?v=1OooVY-k1Dc",
            "embed_url": "https://www.youtube.com/embed/1OooVY-k1Dc",
            "objective": "Derivare la formula che permette di calcolare un integrale definito attraverso una primitiva della funzione integranda.",
            "prerequisite": "Il teorema fondamentale del calcolo integrale: se F è la funzione integrale di f, allora F′(x) = f(x).",
            "steps": [
                "Si definisce φ(x) come una qualunque primitiva di f(x), quindi φ′(x) = f(x).",
                "F(x) e φ(x) hanno la stessa derivata. Differiscono quindi per una costante: F(x) = φ(x) + k.",
                "Si valuta in x = a. Per definizione F(a) = ∫ₐᵃ f(t)dt = 0.",
                "Da 0 = φ(a) + k si ricava k = −φ(a).",
                "Si sostituisce la costante: F(x) = φ(x) − φ(a).",
                "Si valuta in x = b e si ottiene ∫ₐᵇ f(x)dx = φ(b) − φ(a)."
            ],
            "result_formula": "∫ₐᵇ f(x)dx = φ(b) − φ(a)",
            "result_note": "Trova una qualunque primitiva e calcolane la differenza agli estremi. La costante additiva si elide.",
            "synthesis_note": "La spiegazione orale stabilisce il collegamento logico tra derivazione e integrazione. La lavagna conferma ogni trasformazione tramite le formule scritte progressivamente.",
            "tags": ["Teorema fondamentale", "Funzione primitiva", "Integrale definito", "Costante di integrazione"],
            "segments": [
                ["00:00", 0, "Ciao a tutti. Oggi vediamo la formula di Newton–Leibniz, che ci servirà per risolvere un integrale definito."],
                ["00:25", 25, "Noi sappiamo che la derivata della funzione integrale è uguale alla funzione integranda."],
                ["01:41", 101, "La funzione integranda è data dalla derivata della primitiva. Detta φ(x) la primitiva di f(x), φ'(x) deve essere uguale a f(x)."],
                ["02:51", 171, "Due funzioni che hanno la stessa derivata differiscono per una costante additiva."],
                ["04:37", 277, "Quando i due estremi di integrazione coincidono, l’integrale si annulla."],
                ["06:47", 407, "Otteniamo così la formula di Newton–Leibniz: l’integrale da a a b è uguale a φ(b) meno φ(a)."],
                ["07:39", 459, "La formula ci permette di calcolare direttamente un integrale definito attraverso una qualunque primitiva."]
            ],
            "ocr_lines": [
                "F′(x) = f(x)",
                "φ′(x) = f(x)",
                "F(x) = φ(x) + k",
                "F(a) = ∫ₐᵃ f(x)dx = 0",
                "k = −φ(a)",
                "∫ₐᵇ f(x)dx = φ(b) − φ(a)"
            ],
            "visual_description": "Il docente si trova davanti a una lavagna nera e costruisce la dimostrazione cancellando e riscrivendo le formule in sequenza algebrica.",
            "document_markdown": "# Formula di Newton–Leibniz per il calcolo degli integrali definiti\n\n## Obiettivo del tutorial\nDerivare la formula che permette di calcolare un integrale definito attraverso una primitiva della funzione integranda.\n\n## Prerequisito\nIl teorema fondamentale del calcolo integrale: se F è la funzione integrale di f, allora F′(x) = f(x).\n\n## Procedura completa\n1. Si definisce φ(x) come una qualunque primitiva di f(x), quindi φ′(x) = f(x).\n2. F(x) e φ(x) hanno la stessa derivata: F(x) = φ(x) + k.\n3. In x = a vale F(a) = ∫ₐᵃ f(t)dt = 0, da cui k = −φ(a).\n4. In x = b si ottiene ∫ₐᵇ f(x)dx = φ(b) − φ(a).\n",
            "manifest": {
                "package_version": "1.0",
                "content_kind": "tutorial",
                "artifacts": {
                    "document": "document.md",
                    "transcript": "transcript.md",
                    "visual": "visual.md"
                },
                "verification": {
                    "scope": "structural",
                    "semantic_equivalence": "not_verified",
                    "segments_total": 7,
                    "segments_covered": 7,
                    "audio_evidence_present": True,
                    "visual_evidence_present": True,
                    "timestamps_monotonic": True
                },
                "generator": {
                    "model": "gemini-2.5-flash",
                    "cost_usd": 0.0042
                }
            }
        },
        {
            "id": "instagram_dark_mode_explanation",
            "title": "Perché il Dark Mode è sopravvalutato",
            "full_title": "Perché il Dark Mode è sopravvalutato nei prodotti SaaS B2B e la chiarezza batte l'estetica",
            "eyebrow": "Product Design · 9 marzo 2026",
            "meta": "Instagram · Edward Builds",
            "author": "Edward Builds",
            "dur": "01:26",
            "duration_label": "1 min",
            "language": "Italiano",
            "src": "instagram",
            "long": False,
            "content_kind": "explanation",
            "source_url": "https://www.instagram.com/p/DVqG5MnjDEV/",
            "embed_url": "",
            "objective": "Spiegare perché implementare il dark mode troppo presto raddoppia il costo di manutenzione del design system senza migliorare i flussi critici.",
            "prerequisite": "Gestione di token colore semantici, contrasto tipografico e QA visivo su componenti data-dense.",
            "steps": [
                "Solo metà degli utenti che chiedono il dark mode lo mantiene attivo sui tool di lavoro quotidiani (50%).",
                "Meno del 12% degli utenti apre le impostazioni del tema dopo il primo onboarding.",
                "Ogni nuovo stato (hover, focus, alert, badge, tabella) raddoppia il costo di manutenzione e testing QA.",
                "Nei flussi critici ad alta densità informativa, la chiarezza e la gerarchia su fondo chiaro battono l'estetica dark."
            ],
            "result_formula": "Chiarezza > Doppio Tema prematuro",
            "result_note": "Investi prima sulla leggibilità dei flussi critici; introduci un secondo tema solo quando il design system è stabile.",
            "synthesis_note": "Il reel confronta metriche reali di adozione (50% uso effettivo, 12% apertura impostazioni) con gli screenshot di tabelle B2B rotte da contrasti dark improvvisati.",
            "tags": ["Design System", "Dark Mode", "Costo di manutenzione", "Flussi critici"],
            "segments": [
                ["00:00", 0, "Tutti chiedono il dark mode nei commenti, ma solo metà lo usa davvero ogni giorno."],
                ["00:19", 19, "Nei nostri dati meno del 12% degli utenti tocca le impostazioni del tema dopo la prima settimana."],
                ["00:44", 44, "In compenso ogni componente, badge e grafico costa il doppio in manutenzione e QA."],
                ["01:08", 68, "Sui flussi critici la chiarezza batte sempre l'estetica: sistema prima la gerarchia su un solo tema."]
            ],
            "ocr_lines": [
                "50% adoption vs requests",
                "12% open theme settings",
                "2x maintenance cost per component",
                "Clarity beats aesthetics on critical flows"
            ],
            "visual_description": "Sequenza rapida di card comparative: a sinistra una tabella finanziaria leggibile su sfondo chiaro, a destra stati di errore e bordi illeggibili su dark mode non testato.",
            "document_markdown": "# Perché il Dark Mode è sopravvalutato nei prodotti SaaS B2B\n\n## Tesi centrale\nImplementare il dark mode prima di aver stabilizzato i flussi critici aumenta i costi di manutenzione senza impatto reale sulla ritenzione.\n\n## Evidenze quantitative\n1. Solo metà (50%) di chi lo richiede lo usa stabilmente.\n2. Il 12% apre le impostazioni del tema.\n3. Il costo di manutenzione di token e stati UI raddoppia.\n4. Nei flussi critici la chiarezza batte l'estetica.\n",
            "manifest": {
                "package_version": "1.0",
                "content_kind": "explanation",
                "artifacts": {
                    "document": "document.md",
                    "transcript": "transcript.md",
                    "visual": "visual.md"
                },
                "verification": {
                    "scope": "structural",
                    "semantic_equivalence": "not_verified",
                    "segments_total": 4,
                    "segments_covered": 4,
                    "audio_evidence_present": True,
                    "visual_evidence_present": True,
                    "timestamps_monotonic": True
                },
                "generator": {
                    "model": "gemini-2.5-flash",
                    "cost_usd": 0.0019
                }
            }
        },
        {
            "id": "instagram_bliss_narrative",
            "title": "La storia dietro lo sfondo Bliss di Windows XP",
            "full_title": "Come un'infestazione di fillossera in Napa Valley ha creato la fotografia più vista della storia (Bliss, Windows XP)",
            "eyebrow": "Storia della Tecnologia · 16 aprile 2026",
            "meta": "Instagram · Edward Builds",
            "author": "Edward Builds",
            "dur": "02:14",
            "duration_label": "2 min",
            "language": "Italiano",
            "src": "instagram",
            "long": True,
            "content_kind": "narrative",
            "source_url": "https://www.instagram.com/p/DXNM6Pfj8zy/",
            "embed_url": "",
            "objective": "Ricostruire la catena storica verificabile dietro lo scatto originale di Charles O'Rear del 1996 e l'acquisizione da parte di Microsoft per Windows XP (2001).",
            "prerequisite": "Nessun prerequisito tecnico; caso di studio su licensing fotografico e distribuzione software di massa.",
            "steps": [
                "Anni '90: un'infestazione di fillossera costringe a espiantare i vigneti su una collina di Sonoma/Napa Valley, lasciando un prato verde intatto.",
                "Gennaio 1996: il fotografo Charles O'Rear scatta la foto su pellicola medio formato Mamiya RZ67 senza ritocco digitale.",
                "O'Rear carica lo scatto sull'agenzia Corbis (fondata da Bill Gates).",
                "Nel 2000–2001 Microsoft acquista i diritti completi per la campagna di Windows XP.",
                "Il valore assicurativo della pellicola originale è così alto che i corrieri rifiutano la spedizione: O'Rear porta il negativo a Seattle in aereo di persona.",
                "Dal rilascio di Windows XP nel 2001, lo scatto viene visto da oltre 1 miliardo di persone."
            ],
            "result_formula": "1996 Pellicola Mamiya RZ67 → Corbis → Windows XP (2001, >1 miliardo di utenti)",
            "result_note": "Lo scatto non fu commissionato da Microsoft né modificato in digitale: nacque da una pausa temporanea dei vigneti dopo l'infestazione.",
            "synthesis_note": "La narrazione unisce fatti agronomici (infestazione di fillossera), dettagli fotografici (pellicola Fujifilm Velvia su Mamiya RZ67) e logistica di consegna del negativo originale.",
            "tags": ["Windows XP", "Charles O'Rear", "Corbis", "Microsoft", "Storia Tech"],
            "segments": [
                ["00:00", 0, "La collina più famosa del mondo non doveva nemmeno essere un prato: era un vigneto distrutto da un'infestazione."],
                ["00:28", 28, "Nel gennaio 1996 Charles O'Rear passa di lì e scatta su pellicola medio formato, zero Photoshop."],
                ["00:58", 58, "Carica la foto su Corbis; anni dopo Microsoft la sceglie per il lancio di Windows XP nel 2001."],
                ["01:32", 92, "Nessun corriere voleva coprire l'assicurazione del negativo originale, così O'Rear prese un volo per portarlo a mano."],
                ["01:56", 116, "Da quel momento oltre un miliardo di persone ha visto quella collina accendendo il computer."]
            ],
            "ocr_lines": [
                "Sonoma County, 1996 — Phylloxera infestation",
                "Charles O'Rear · Mamiya RZ67 (no digital edit)",
                "Licensed via Corbis -> Microsoft Windows XP (2001)",
                "Courier insurance limit exceeded -> hand-delivered to Seattle",
                "1+ billion viewers worldwide"
            ],
            "visual_description": "Confronto tra la collina verde del 1996 su pellicola Velvia e la stessa collina tornata a vigneto anni dopo, seguito da inquadrature della fotocamera Mamiya RZ67.",
            "document_markdown": "# La storia dietro lo sfondo Bliss di Windows XP\n\n## Cronologia verificata\n1. Un'infestazione di fillossera porta all'espianto temporaneo dei vigneti.\n2. Charles O'Rear scatta la fotografia nel 1996 su medio formato e la carica su Corbis.\n3. Microsoft acquisisce lo scatto per Windows XP (2001).\n4. Per limiti di assicurazione dei corrieri, O'Rear consegna il negativo di persona.\n5. Oltre un miliardo di persone ha visto lo scatto.\n",
            "manifest": {
                "package_version": "1.0",
                "content_kind": "narrative",
                "artifacts": {
                    "document": "document.md",
                    "transcript": "transcript.md",
                    "visual": "visual.md"
                },
                "verification": {
                    "scope": "structural",
                    "semantic_equivalence": "not_verified",
                    "segments_total": 5,
                    "segments_covered": 5,
                    "audio_evidence_present": True,
                    "visual_evidence_present": True,
                    "timestamps_monotonic": True
                },
                "generator": {
                    "model": "gemini-2.5-flash",
                    "cost_usd": 0.0027
                }
            }
        }
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"version": "1.0", "samples": samples}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="kn5 CLI: scarica un Reel/Carosello Instagram (o video YouTube), "
        "analizza con Gemini, salva in library/ come markdown."
    )
    parser.add_argument(
        "url", nargs="?", default=None,
        help="URL del contenuto (oppure comando 'export-demo' o variabile d'ambiente REEL_URL)"
    )
    parser.add_argument(
        "--html", action="store_true",
        help="Genera anche il vecchio report HTML in output/"
    )
    parser.add_argument(
        "--out", default="samples.json",
        help="Percorso di output per il comando export-demo (default: samples.json)"
    )
    args = parser.parse_args()

    if args.url == "export-demo":
        out = export_demo_samples(Path(args.out))
        print(f"✅ Esportati 3 pacchetti demo in: {out}")
        return

    url = get_url(args.url)
    if not url:
        print("Errore: fornisci un URL (argomento o variabile REEL_URL).", file=sys.stderr)
        sys.exit(1)

    process_url(url, write_html=args.html)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
