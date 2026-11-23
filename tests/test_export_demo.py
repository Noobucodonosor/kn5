import json
import tempfile
import unittest
from pathlib import Path

from main import export_demo_samples


class ExportDemoTests(unittest.TestCase):
    def test_export_demo_writes_three_distinct_sanitized_packages_with_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_file = Path(tmp) / "samples.json"
            result_path = export_demo_samples(out_file)
            self.assertEqual(result_path, out_file)
            self.assertTrue(out_file.exists())

            raw = out_file.read_text(encoding="utf-8")
            self.assertNotIn("AIza", raw)
            self.assertNotIn("PRIVATE KEY", raw)

            payload = json.loads(raw)
            self.assertEqual(payload["version"], "1.0")
            samples = payload["samples"]
            self.assertEqual(len(samples), 3)

            ids = {s["id"] for s in samples}
            self.assertEqual(len(ids), 3)

            for sample in samples:
                self.assertTrue(sample["document_markdown"].strip())
                self.assertGreater(len(sample["segments"]), 0)
                self.assertGreater(len(sample["ocr_lines"]), 0)
                manifest = sample["manifest"]
                self.assertEqual(manifest["verification"]["scope"], "structural")
                self.assertEqual(manifest["verification"]["semantic_equivalence"], "not_verified")
                self.assertEqual(
                    set(manifest["artifacts"].keys()),
                    {"document", "transcript", "visual"},
                )


if __name__ == "__main__":
    unittest.main()
