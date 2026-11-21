import json
import tempfile
import unittest
from pathlib import Path

from src.skills.library_writer import LibraryWriter


class ArtifactPackageTests(unittest.TestCase):
    def test_writes_four_separate_auditable_artifacts(self):
        process_map = {
            "title": "Tutorial di prova",
            "content_kind": "tutorial",
            "textual_document": "## Procedura completa\n\nApri il file e verifica il risultato.",
            "executive_summary": "Una procedura completa.",
            "steps": [{"step_number": 1, "title": "Apri", "description": "Apri il file.", "key_actions": []}],
            "epistemic_insights": [],
            "transcript": "[00:01] parole pronunciate",
            "ocr_text": "[00:02] testo sullo schermo",
            "visual_description": "Si vede un editor.",
            "language": "it",
            "tags": ["prova"],
        }
        metadata = {
            "url": "https://youtube.com/watch?v=abc",
            "source": "youtube",
            "media_type": "video",
            "profile": "canale",
            "shortcode": "abc",
            "date": "2026-09-01",
        }
        verification = {"scope": "structural", "semantic_equivalence": "not_verified"}

        with tempfile.TemporaryDirectory() as directory:
            paths = LibraryWriter(directory).write_artifact_package(
                process_map,
                metadata,
                verification,
                cost_usd=0.01,
                generator_model="gemini-test",
            )

            self.assertEqual(set(paths), {"document", "transcript", "visual", "manifest"})
            self.assertTrue(all(path.exists() for path in paths.values()))
            document = paths["document"].read_text()
            transcript = paths["transcript"].read_text()
            visual = paths["visual"].read_text()
            manifest = json.loads(paths["manifest"].read_text())

            self.assertIn("Apri il file e verifica il risultato", document)
            self.assertNotIn("## Sintesi", document)
            self.assertNotIn("parole pronunciate", document)
            self.assertNotIn("testo sullo schermo", document)
            self.assertIn("parole pronunciate", transcript)
            self.assertIn("testo sullo schermo", visual)
            self.assertEqual(manifest["artifacts"]["document"], "document.md")
            self.assertEqual(manifest["verification"], verification)
            self.assertEqual(manifest["generator"]["model"], "gemini-test")
            self.assertEqual(manifest["content_kind"], "tutorial")

            index = Path(directory, "profiles", "canale", "index.md").read_text()
            self.assertIn("/document|Tutorial di prova", index)


if __name__ == "__main__":
    unittest.main()
