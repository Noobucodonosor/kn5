"""Valutazione offline dei pacchetti KN5 contro casi di riferimento."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

_TIMESTAMP = re.compile(r"\[(?:\d{1,3}:\d{2}|slide\s+\d+)\]", re.IGNORECASE)


def _markdown_body(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    return parts[2] if len(parts) == 3 else text


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = text.translate(str.maketrans({"−": "-", "–": "-", "—": "-"}))
    return " ".join(text.split())


def _find_concept(text: str, variants: list[str]) -> int:
    positions = [_normalize(text).find(_normalize(variant)) for variant in variants]
    present = [position for position in positions if position >= 0]
    return min(present) if present else -1


def evaluate_case(case: dict, package_dir: Path) -> dict:
    """Valuta un singolo pacchetto con controlli lessicali e di contratto."""

    required_files = ["document.md", "transcript.md", "visual.md", "manifest.json"]
    missing_files = [name for name in required_files if not (package_dir / name).is_file()]
    if missing_files:
        return {
            "id": case["id"],
            "passed": False,
            "checks": {"package_complete": False},
            "details": {"missing_files": missing_files},
            "scope": "golden_lexical_acceptance",
            "semantic_equivalence": "not_proven",
        }

    document = _markdown_body((package_dir / "document.md").read_text(encoding="utf-8"))
    transcript = _markdown_body((package_dir / "transcript.md").read_text(encoding="utf-8"))
    visual = _markdown_body((package_dir / "visual.md").read_text(encoding="utf-8"))
    manifest = json.loads((package_dir / "manifest.json").read_text(encoding="utf-8"))
    normalized_document = _normalize(document)

    concept_positions = {
        concept["id"]: _find_concept(document, concept["any_of"])
        for concept in case.get("concepts", [])
    }
    ordered_positions = [
        concept_positions.get(concept_id, -1)
        for concept_id in case.get("ordered_concepts", [])
    ]
    forbidden_found = [
        term for term in case.get("forbidden_terms", [])
        if _normalize(term) in normalized_document
    ]

    checks = {
        "package_complete": True,
        "content_kind_matches": manifest.get("content_kind") == case["expected_content_kind"],
        "document_long_enough": len(document.strip()) >= int(case.get("min_document_chars", 0)),
        "required_concepts_present": all(position >= 0 for position in concept_positions.values()),
        "concept_order_preserved": (
            all(position >= 0 for position in ordered_positions)
            and ordered_positions == sorted(ordered_positions)
        ) if ordered_positions else True,
        "forbidden_terms_absent": not forbidden_found,
        "transcript_evidence_present": (
            bool(_TIMESTAMP.search(transcript)) if case.get("require_transcript") else True
        ),
        "visual_evidence_present": (
            bool(_TIMESTAMP.search(visual)) if case.get("require_visual") else True
        ),
    }
    return {
        "id": case["id"],
        "passed": all(checks.values()),
        "checks": checks,
        "details": {
            "missing_concepts": [key for key, position in concept_positions.items() if position < 0],
            "forbidden_terms_found": forbidden_found,
            "document_chars": len(document.strip()),
        },
        "scope": "golden_lexical_acceptance",
        "semantic_equivalence": "not_proven",
    }


def evaluate_suite(cases_file: Path, packages_root: Path) -> dict:
    suite = json.loads(cases_file.read_text(encoding="utf-8"))
    results = []
    skipped = []
    for case in suite["cases"]:
        if not case.get("enabled", True):
            skipped.append({"id": case["id"], "reason": case.get("disabled_reason", "disabled")})
            continue
        results.append(evaluate_case(case, packages_root / case["id"]))
    return {
        "version": suite["version"],
        "passed": bool(results) and all(result["passed"] for result in results),
        "results": results,
        "skipped": skipped,
        "scope": "golden_lexical_acceptance",
        "semantic_equivalence": "requires_human_review",
    }
