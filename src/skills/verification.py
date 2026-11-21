"""Controlli strutturali sugli output multimediali di KN5.

Questi controlli misurano completezza e forma delle evidenze disponibili. Non
possono dimostrare da soli che il documento sia semanticamente equivalente al
contenuto originale; il risultato lo dichiara esplicitamente.
"""

from __future__ import annotations

import re

_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{2})\]")


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _has_source_evidence(process_map: dict) -> bool:
    return any(
        _text(process_map.get(field))
        for field in ("transcript", "ocr_text", "visual_description")
    )


def _has_document_content(process_map: dict) -> bool:
    return bool(
        _text(process_map.get("textual_document"))
        or _text(process_map.get("executive_summary"))
        or process_map.get("steps")
        or process_map.get("epistemic_insights")
    )


def verify_process_map(
    process_map: dict,
    *,
    media_type: str,
    expected_segments: int,
    segment_maps: list[dict],
) -> dict:
    """Verifica completezza strutturale, copertura segmenti e timestamp."""

    transcript = _text(process_map.get("transcript"))
    ocr_text = _text(process_map.get("ocr_text"))
    visual = _text(process_map.get("visual_description"))
    textual_document = _text(process_map.get("textual_document"))
    timestamp_matches = _TIMESTAMP.findall(f"{transcript}\n{ocr_text}")
    invalid_timestamps = [f"{minutes}:{seconds}" for minutes, seconds in timestamp_matches if int(seconds) >= 60]
    segments_with_evidence = sum(_has_source_evidence(part) for part in segment_maps)

    checks = {
        "segments_count_matches": expected_segments > 0 and len(segment_maps) == expected_segments,
        "every_segment_has_evidence": (
            expected_segments > 0 and segments_with_evidence == expected_segments
        ),
        "title_present": bool(_text(process_map.get("title"))),
        "document_content_present": _has_document_content(process_map),
        "source_evidence_present": _has_source_evidence(process_map),
        "timestamps_valid": not invalid_timestamps,
    }

    warnings = []
    if media_type == "video" and transcript and not timestamp_matches:
        warnings.append("Trascrizione presente ma senza timestamp verificabili")
    if media_type == "video" and not transcript:
        warnings.append("Nessuna trascrizione audio; verificare se il video è privo di parlato")
    if not ocr_text:
        warnings.append("Nessun testo on-screen rilevato")

    return {
        "passed": all(checks.values()),
        "scope": "structural",
        "semantic_equivalence": "not_verified",
        "checks": checks,
        "metrics": {
            "expected_segments": expected_segments,
            "segments_with_evidence": segments_with_evidence,
            "transcript_chars": len(transcript),
            "ocr_chars": len(ocr_text),
            "visual_chars": len(visual),
            "document_chars": len(textual_document),
            "timestamps_found": len(timestamp_matches),
        },
        "warnings": warnings,
    }
