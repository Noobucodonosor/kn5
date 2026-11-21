"""
LibraryWriter: salva un pezzo (Reel/post/carosello/video) come file markdown
strutturato nella biblioteca personale.

Struttura su disco:

    library/
    ├── .budget.json                       (gestito da BudgetTracker)
    ├── inbox/                             pezzi senza profilo identificato
    │   └── 2026-06-17_C123_titolo.md
    └── profiles/
        └── <profile-slug>/
            ├── index.md                   catalogo aggiornato a ogni write
            └── 2026-06-17_C123_titolo.md

Il markdown ha frontmatter YAML completo + sezioni esplicite (sintesi, fasi,
concetti, trascrizione, OCR, caption). L'obiettivo è essere
contemporaneamente leggibile in Obsidian e digeribile da un LLM.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

logger = logging.getLogger(__name__)


def _slugify(text: str, max_len: int = 50) -> str:
    """Slug ascii-safe per nomi file e cartelle."""
    text = (text or "").strip().lower()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len] or "senza-titolo"


def _yaml_value(v) -> str:
    """Serializza un valore in YAML inline-safe (string, int, list, None)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        if not v:
            return "[]"
        # Lista inline di stringhe / numeri
        items = [_yaml_value(x) if not isinstance(x, str) else _yaml_scalar(x) for x in v]
        return "[" + ", ".join(items) + "]"
    return _yaml_scalar(str(v))


def _yaml_scalar(s: str) -> str:
    """Quota una stringa per YAML inline se contiene caratteri ambigui."""
    needs_quote = any(c in s for c in ":#&*!|>'%@`{}[],?") or s.startswith(("- ", "? "))
    if needs_quote or s != s.strip():
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


class LibraryWriter:
    def __init__(self, library_root: Union[str, Path] = "library"):
        self.root = Path(library_root)
        self.inbox = self.root / "inbox"
        self.profiles = self.root / "profiles"
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.profiles.mkdir(parents=True, exist_ok=True)

    # =================================================================
    # Public API
    # =================================================================

    def write_piece(
        self,
        process_map: Dict,
        metadata: Dict,
        cost_usd: Optional[float] = None,
    ) -> Path:
        """
        Scrive un singolo pezzo come .md e aggiorna l'index del profilo (se noto).

        Args:
            process_map: dict ProcessMap restituito da KnowledgeSynthesizer.
            metadata: dict da MediaFetcher.extract_metadata().
            cost_usd: costo stimato della chiamata Gemini (per frontmatter).

        Returns:
            Path del file markdown scritto.
        """
        profile = metadata.get("profile")
        target_dir = self._target_dir(profile)
        target_dir.mkdir(parents=True, exist_ok=True)

        filename = self._filename(process_map, metadata)
        path = target_dir / filename

        md = self._render_markdown(process_map, metadata, cost_usd)
        path.write_text(md, encoding="utf-8")
        logger.info("Library: scritto %s", path)

        if profile:
            self._update_profile_index(profile)

        return path

    def write_artifact_package(
        self,
        process_map: Dict,
        metadata: Dict,
        verification: Dict,
        cost_usd: Optional[float] = None,
        generator_model: Optional[str] = None,
    ) -> Dict[str, Path]:
        """Scrive documento, fonti letterali e manifest in una cartella dedicata."""

        profile = metadata.get("profile")
        target_dir = self._target_dir(profile)
        package_dir = target_dir / Path(self._filename(process_map, metadata)).stem
        package_dir.mkdir(parents=True, exist_ok=True)

        document = self._render_markdown(
            process_map,
            metadata,
            cost_usd,
            include_literal_sources=False,
        )
        transcript = self._render_source_artifact(
            "transcript",
            process_map,
            metadata,
            [("Trascrizione audio", process_map.get("transcript"))],
        )
        visual = self._render_source_artifact(
            "visual",
            process_map,
            metadata,
            [
                ("Testo on-screen (OCR)", process_map.get("ocr_text")),
                ("Descrizione visiva", process_map.get("visual_description")),
            ],
        )
        manifest = {
            "schema_version": "1.0",
            "title": process_map.get("title") or metadata.get("title"),
            "content_kind": process_map.get("content_kind") or "other",
            "source": {
                "url": metadata.get("url"),
                "platform": metadata.get("source"),
                "media_type": metadata.get("media_type"),
                "profile": metadata.get("profile"),
                "shortcode": metadata.get("shortcode"),
            },
            "generator": {"model": generator_model},
            "artifacts": {
                "document": "document.md",
                "transcript": "transcript.md",
                "visual": "visual.md",
                "manifest": "manifest.json",
            },
            "verification": verification,
            "gemini_cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
        }

        paths = {
            "document": package_dir / "document.md",
            "transcript": package_dir / "transcript.md",
            "visual": package_dir / "visual.md",
            "manifest": package_dir / "manifest.json",
        }
        paths["document"].write_text(document, encoding="utf-8")
        paths["transcript"].write_text(transcript, encoding="utf-8")
        paths["visual"].write_text(visual, encoding="utf-8")
        paths["manifest"].write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info("Library: scritto pacchetto %s", package_dir)

        if profile:
            self._update_profile_index(profile)
        return paths

    # =================================================================
    # Helpers
    # =================================================================

    def _target_dir(self, profile: Optional[str]) -> Path:
        if profile:
            return self.profiles / _slugify(profile, max_len=40)
        return self.inbox

    def _filename(self, process_map: Dict, metadata: Dict) -> str:
        date = metadata.get("date") or datetime.now().strftime("%Y-%m-%d")
        shortcode = metadata.get("shortcode") or ""
        title = process_map.get("title") or metadata.get("title") or "senza-titolo"
        slug = _slugify(title, max_len=50)
        sc_part = f"_{shortcode}" if shortcode else ""
        return f"{date}{sc_part}_{slug}.md"

    # =================================================================
    # Markdown rendering
    # =================================================================

    def _render_markdown(
        self,
        pm: Dict,
        meta: Dict,
        cost_usd: Optional[float],
        include_literal_sources: bool = True,
    ) -> str:
        # Frontmatter
        front: Dict = {
            "url": meta.get("url"),
            "source": meta.get("source"),
            "shortcode": meta.get("shortcode"),
            "profile": meta.get("profile"),
            "media_type": meta.get("media_type"),
            "date": meta.get("date"),
            "ingested_at": datetime.now().strftime("%Y-%m-%dT%H:%M"),
            "title": pm.get("title"),
            "content_kind": pm.get("content_kind"),
            "language": pm.get("language"),
            "tags": pm.get("tags") or [],
            "hashtags": meta.get("hashtags") or [],
            "mentions": meta.get("mentions") or [],
            "duration_sec": meta.get("duration_sec"),
            "like_count": meta.get("like_count"),
            "view_count": meta.get("view_count"),
            "comment_count": meta.get("comment_count"),
            "gemini_cost_usd": round(cost_usd, 6) if cost_usd is not None else None,
        }
        # Rimuovi chiavi None per non sporcare il frontmatter
        front = {k: v for k, v in front.items() if v is not None and v != []}

        lines: List[str] = ["---"]
        for k, v in front.items():
            lines.append(f"{k}: {_yaml_value(v)}")
        lines.append("---")
        lines.append("")

        title = pm.get("title") or meta.get("title") or "(senza titolo)"
        lines.append(f"# {title}")
        lines.append("")

        textual_document = (pm.get("textual_document") or "").strip()
        if textual_document:
            lines += [textual_document, ""]

        # Sintesi
        summary = "" if textual_document else (pm.get("executive_summary") or "").strip()
        if summary:
            lines += ["## Sintesi", "", summary, ""]

        # Fasi
        steps = [] if textual_document else (pm.get("steps") or [])
        if steps:
            lines += ["## Fasi del processo", ""]
            for s in steps:
                n = s.get("step_number", "?")
                t = s.get("title", "")
                d = (s.get("description") or "").strip()
                lines.append(f"### {n}. {t}")
                if d:
                    lines.append(d)
                actions = s.get("key_actions") or []
                if actions:
                    lines += ["", "**Azioni chiave:**"]
                    for a in actions:
                        lines.append(f"- {a}")
                lines.append("")

        # Concetti chiave
        insights = [] if textual_document else (pm.get("epistemic_insights") or [])
        if insights:
            lines += ["## Concetti chiave", ""]
            for ins in insights:
                c = ins.get("concept", "")
                e = (ins.get("explanation") or "").strip()
                lines.append(f"- **{c}**: {e}")
            lines.append("")

        if include_literal_sources:
            # Trascrizione (verbatim)
            transcript = (pm.get("transcript") or "").strip()
            if transcript:
                lines += ["## Trascrizione audio (verbatim)", "", transcript, ""]

            # OCR / Testo on-screen
            ocr = (pm.get("ocr_text") or "").strip()
            if ocr:
                lines += ["## Testo on-screen (OCR)", "", ocr, ""]

            # Descrizione visiva
            visual = (pm.get("visual_description") or "").strip()
            if visual:
                lines += ["## Descrizione visiva", "", visual, ""]

        # Caption originale
        caption = (meta.get("caption") or "").strip()
        if caption:
            lines += ["## Caption originale", ""]
            for line in caption.split("\n"):
                lines.append(f"> {line}" if line.strip() else ">")
            lines.append("")

        # Footer / link sorgente
        url = meta.get("url")
        if url:
            lines += ["---", "", f"**Fonte originale:** [{url}]({url})", ""]

        return "\n".join(lines)

    def _render_source_artifact(
        self,
        artifact_type: str,
        pm: Dict,
        meta: Dict,
        sections: List[tuple[str, object]],
    ) -> str:
        """Renderizza una fonte letterale separata, leggibile da persone e LLM."""

        front = {
            "artifact_type": artifact_type,
            "url": meta.get("url"),
            "source": meta.get("source"),
            "shortcode": meta.get("shortcode"),
            "profile": meta.get("profile"),
            "media_type": meta.get("media_type"),
            "title": pm.get("title") or meta.get("title"),
            "content_kind": pm.get("content_kind"),
            "language": pm.get("language"),
        }
        lines = ["---"]
        for key, value in front.items():
            if value is not None:
                lines.append(f"{key}: {_yaml_value(value)}")
        lines += ["---", "", f"# {front['title'] or '(senza titolo)'} — {artifact_type}", ""]

        has_content = False
        for heading, value in sections:
            content = value.strip() if isinstance(value, str) else ""
            if content:
                has_content = True
                lines += [f"## {heading}", "", content, ""]
        if not has_content:
            lines += ["_Nessuna evidenza di questo tipo è stata estratta._", ""]

        if meta.get("url"):
            lines += ["---", "", f"**Fonte originale:** [{meta['url']}]({meta['url']})", ""]
        return "\n".join(lines)

    # =================================================================
    # Profile index
    # =================================================================

    def _update_profile_index(self, profile: str) -> None:
        """Riscrive l'index.md del profilo elencando tutti i pezzi presenti."""
        prof_dir = self.profiles / _slugify(profile, max_len=40)
        if not prof_dir.exists():
            return

        pieces = sorted(
            [
                *(p for p in prof_dir.glob("*.md") if p.name != "index.md"),
                *prof_dir.glob("*/document.md"),
            ],
            key=lambda p: p.name,
            reverse=True,
        )

        lines: List[str] = [
            f"# Profilo: {profile}",
            "",
            f"_{len(pieces)} pezzi indicizzati. Ultimo aggiornamento: "
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')}_",
            "",
            "## Pezzi",
            "",
        ]

        for p in pieces:
            title = self._extract_title(p)
            summary = self._extract_summary(p)
            link_text = title or p.stem
            relative = p.relative_to(prof_dir).with_suffix("").as_posix()
            line = f"- [[{relative}|{link_text}]]"
            if summary:
                line += f" — {summary}"
            lines.append(line)

        (prof_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def _extract_title(path: Path) -> Optional[str]:
        """Legge la riga `title:` dal frontmatter, fallback al primo `# `."""
        try:
            with open(path, encoding="utf-8") as f:
                in_front = False
                for line in f:
                    line = line.rstrip("\n")
                    if line.strip() == "---":
                        if not in_front:
                            in_front = True
                            continue
                        return None
                    if in_front and line.startswith("title:"):
                        v = line[len("title:"):].strip()
                        if v.startswith('"') and v.endswith('"'):
                            v = v[1:-1].replace('\\"', '"')
                        return v
                    if not in_front and line.startswith("# "):
                        return line[2:].strip()
        except OSError:
            return None
        return None

    @staticmethod
    def _extract_summary(path: Path, max_len: int = 140) -> Optional[str]:
        """Estrae la prima frase non vuota della sezione `## Sintesi`."""
        try:
            with open(path, encoding="utf-8") as f:
                in_summary = False
                for line in f:
                    line = line.strip()
                    if line.startswith("## Sintesi"):
                        in_summary = True
                        continue
                    if in_summary:
                        if not line:
                            continue
                        if line.startswith("##"):
                            return None
                        s = line.strip()
                        return s[:max_len] + ("…" if len(s) > max_len else "")
        except OSError:
            return None
        return None
