import unittest

from pydantic import ValidationError

from src.skills.content_schema import ProcessMap


def valid_payload():
    return {
        "title": "Come preparare il pane",
        "content_kind": "tutorial",
        "textual_document": "## Procedura\n\nImpasta, lascia lievitare e cuoci.",
        "executive_summary": "Preparazione del pane.",
        "steps": [],
        "epistemic_insights": [],
        "transcript": "[00:01] Impastiamo",
        "ocr_text": "",
        "visual_description": "L'impasto raddoppia di volume.",
        "language": "it",
        "tags": ["pane"],
    }


class ContentSchemaTests(unittest.TestCase):
    def test_accepts_genre_adaptive_document(self):
        result = ProcessMap.model_validate(valid_payload())
        self.assertEqual(result.content_kind, "tutorial")
        self.assertIn("Impasta", result.textual_document)

    def test_rejects_unknown_content_kind(self):
        payload = valid_payload()
        payload["content_kind"] = "generic_note"
        with self.assertRaises(ValidationError):
            ProcessMap.model_validate(payload)

    def test_requires_textual_document(self):
        payload = valid_payload()
        del payload["textual_document"]
        with self.assertRaises(ValidationError):
            ProcessMap.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
