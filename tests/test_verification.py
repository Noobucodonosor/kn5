import unittest

from src.skills.verification import verify_process_map


def process_map(**overrides):
    value = {
        "title": "Una spiegazione",
        "content_kind": "explanation",
        "textual_document": "## Spiegazione\n\nIl ragionamento completo.",
        "executive_summary": "Spiegazione completa del contenuto.",
        "steps": [],
        "epistemic_insights": [{"concept": "tema", "explanation": "dettaglio"}],
        "transcript": "[00:01] Testo parlato",
        "ocr_text": "[00:02] Testo visibile",
        "visual_description": "Una lavagna con una formula.",
    }
    value.update(overrides)
    return value


class VerificationTests(unittest.TestCase):
    def test_non_tutorial_passes_without_steps(self):
        value = process_map(steps=[])
        result = verify_process_map(
            value,
            media_type="video",
            expected_segments=1,
            segment_maps=[value],
        )
        self.assertTrue(result["passed"])
        self.assertNotIn("steps_present", result["checks"])

    def test_missing_evidence_in_one_segment_fails(self):
        complete = process_map()
        empty_segment = process_map(transcript="", ocr_text="", visual_description="")
        result = verify_process_map(
            complete,
            media_type="video",
            expected_segments=2,
            segment_maps=[complete, empty_segment],
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["every_segment_has_evidence"])
        self.assertEqual(result["metrics"]["segments_with_evidence"], 1)

    def test_invalid_timestamp_fails(self):
        value = process_map(transcript="[01:75] timestamp non valido")
        result = verify_process_map(
            value,
            media_type="video",
            expected_segments=1,
            segment_maps=[value],
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["timestamps_valid"])

    def test_result_does_not_claim_semantic_equivalence(self):
        value = process_map()
        result = verify_process_map(
            value,
            media_type="video",
            expected_segments=1,
            segment_maps=[value],
        )
        self.assertEqual(result["scope"], "structural")
        self.assertEqual(result["semantic_equivalence"], "not_verified")


if __name__ == "__main__":
    unittest.main()
