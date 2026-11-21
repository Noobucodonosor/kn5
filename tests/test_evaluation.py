import json
import tempfile
import unittest
from pathlib import Path

from src.skills.evaluation import evaluate_case, evaluate_suite


CASE = {
    "id": "tutorial_case",
    "enabled": True,
    "expected_content_kind": "tutorial",
    "min_document_chars": 30,
    "require_transcript": True,
    "require_visual": True,
    "concepts": [
        {"id": "first", "any_of": ["prerequisito"]},
        {"id": "second", "any_of": ["risultato finale"]},
    ],
    "ordered_concepts": ["first", "second"],
    "forbidden_terms": ["fatto inventato"],
}


def write_package(root: Path, document: str, kind: str = "tutorial") -> None:
    root.mkdir(parents=True)
    (root / "document.md").write_text(document)
    (root / "transcript.md").write_text("# Fonte\n\n[00:01] parole")
    (root / "visual.md").write_text("# Visuale\n\n[00:02] formula")
    (root / "manifest.json").write_text(json.dumps({"content_kind": kind}))


class EvaluationTests(unittest.TestCase):
    def test_complete_grounded_package_passes_automatic_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory, "tutorial_case")
            write_package(
                package,
                "# Tutorial\n\nIl prerequisito viene spiegato prima del risultato finale, con dettagli operativi.",
            )
            result = evaluate_case(CASE, package)
            self.assertTrue(result["passed"])
            self.assertEqual(result["semantic_equivalence"], "not_proven")

    def test_missing_or_reordered_concept_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory, "tutorial_case")
            write_package(package, "# Tutorial\n\nIl risultato finale arriva senza alcuna premessa utile.")
            result = evaluate_case(CASE, package)
            self.assertFalse(result["passed"])
            self.assertFalse(result["checks"]["required_concepts_present"])
            self.assertFalse(result["checks"]["concept_order_preserved"])

    def test_suite_reports_disabled_coverage_gap(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_package(
                root / "tutorial_case",
                "# Tutorial\n\nIl prerequisito viene spiegato prima del risultato finale, con dettagli operativi.",
            )
            cases = root / "cases.json"
            cases.write_text(json.dumps({
                "version": "1.0",
                "cases": [CASE, {"id": "interview", "enabled": False, "disabled_reason": "fonte mancante"}],
            }))
            report = evaluate_suite(cases, root)
            self.assertTrue(report["passed"])
            self.assertEqual(report["skipped"], [{"id": "interview", "reason": "fonte mancante"}])
            self.assertEqual(report["semantic_equivalence"], "requires_human_review")


if __name__ == "__main__":
    unittest.main()
