"""Trasformazioni pure per ricomporre analisi di segmenti multimediali.

Questo modulo non importa Flask, Google Cloud o Gemini: può quindi essere
verificato offline e riutilizzato da qualsiasi entrypoint.
"""

from __future__ import annotations

import re

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2})\]")


def offset_timestamps(text: str, offset_seconds: int) -> str:
    """Sposta i timestamp ``[MM:SS]`` di un segmento sull'asse temporale globale."""

    def replace(match: re.Match[str]) -> str:
        absolute = offset_seconds + int(match.group(1)) * 60 + int(match.group(2))
        return f"[{absolute // 60:02d}:{absolute % 60:02d}]"

    return _TIMESTAMP.sub(replace, text or "")


def merge_process_maps(parts: list[tuple[int, dict]]) -> dict:
    """Unisce analisi ordinate di segmenti senza perdere il contenuto letterale."""

    merged = {
        "title": "",
        "content_kind": "other",
        "textual_document": "",
        "executive_summary": "",
        "steps": [],
        "epistemic_insights": [],
        "transcript": "",
        "ocr_text": "",
        "visual_description": "",
        "language": "it",
        "tags": [],
    }
    usages = {"input_tokens": 0, "output_tokens": 0}
    concepts: set[str] = set()
    tags: set[str] = set()

    for part_no, (offset, data) in enumerate(parts, 1):
        if not merged["title"]:
            merged["title"] = data.get("title", "")
            merged["language"] = data.get("language", "it")
            merged["content_kind"] = data.get("content_kind", "other")

        textual_document = data.get("textual_document", "").strip()
        if textual_document:
            merged["textual_document"] += (
                f"\n\n## Parte {part_no}\n\n{textual_document}"
            )

        summary = data.get("executive_summary", "").strip()
        if summary:
            merged["executive_summary"] += f"\n\n### Parte {part_no}\n{summary}"

        for source_step in data.get("steps", []):
            step = dict(source_step)
            step["step_number"] = len(merged["steps"]) + 1
            merged["steps"].append(step)

        for insight in data.get("epistemic_insights", []):
            key = insight.get("concept", "").strip().lower()
            if key and key not in concepts:
                concepts.add(key)
                merged["epistemic_insights"].append(insight)

        transcript = offset_timestamps(data.get("transcript", ""), offset)
        if transcript:
            merged["transcript"] += ("\n" if merged["transcript"] else "") + transcript

        ocr_text = offset_timestamps(data.get("ocr_text", ""), offset)
        if ocr_text:
            merged["ocr_text"] += ("\n" if merged["ocr_text"] else "") + ocr_text

        visual = data.get("visual_description", "").strip()
        if visual:
            merged["visual_description"] += (
                f"\n[Segmento {part_no} — {offset // 60:02d}:{offset % 60:02d}] {visual}"
            )

        tags.update(data.get("tags", []))
        usage = data.get("_usage", {})
        usages["input_tokens"] += usage.get("input_tokens", 0)
        usages["output_tokens"] += usage.get("output_tokens", 0)

    for key in (
        "textual_document",
        "executive_summary",
        "transcript",
        "ocr_text",
        "visual_description",
    ):
        merged[key] = merged[key].strip()
    merged["tags"] = sorted(tags)[:12]
    merged["_usage"] = usages
    return merged
