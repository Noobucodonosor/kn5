import unittest

from src.skills.process_map import merge_process_maps, offset_timestamps


class ProcessMapMergeTests(unittest.TestCase):
    def test_offsets_timestamps_past_one_hour(self):
        self.assertEqual(
            offset_timestamps("[00:05] testo [12:30] altro", 3600),
            "[60:05] testo [72:30] altro",
        )

    def test_merge_preserves_all_literal_content(self):
        def part(title, transcript, ocr, usage):
            return {
                "title": title,
                "content_kind": "tutorial",
                "textual_document": f"Documento {title}",
                "executive_summary": f"Sintesi {title}",
                "steps": [
                    {
                        "step_number": 1,
                        "title": title,
                        "description": "d",
                        "key_actions": [],
                    }
                ],
                "epistemic_insights": [{"concept": title, "explanation": "e"}],
                "transcript": transcript,
                "ocr_text": ocr,
                "visual_description": f"visivo {title}",
                "language": "it",
                "tags": [title.lower()],
                "_usage": usage,
            }

        merged = merge_process_maps(
            [
                (0, part("Uno", "[00:01] alfa", "[00:02] beta", {"input_tokens": 10, "output_tokens": 2})),
                (900, part("Due", "[00:03] gamma", "[00:04] delta", {"input_tokens": 20, "output_tokens": 3})),
            ]
        )
        self.assertIn("[00:01] alfa", merged["transcript"])
        self.assertIn("[15:03] gamma", merged["transcript"])
        self.assertIn("[15:04] delta", merged["ocr_text"])
        self.assertIn("## Parte 1\n\nDocumento Uno", merged["textual_document"])
        self.assertIn("## Parte 2\n\nDocumento Due", merged["textual_document"])
        self.assertEqual(merged["content_kind"], "tutorial")
        self.assertEqual([s["step_number"] for s in merged["steps"]], [1, 2])
        self.assertEqual(merged["_usage"], {"input_tokens": 30, "output_tokens": 5})

    def test_merge_does_not_mutate_source_steps(self):
        source_step = {
            "step_number": 9,
            "title": "Originale",
            "description": "d",
            "key_actions": [],
        }
        merge_process_maps([(0, {"steps": [source_step]})])
        self.assertEqual(source_step["step_number"], 9)

    def test_empty_parts_produce_a_complete_empty_shape(self):
        merged = merge_process_maps([])
        self.assertEqual(merged["transcript"], "")
        self.assertEqual(merged["textual_document"], "")
        self.assertEqual(merged["steps"], [])
        self.assertEqual(merged["_usage"], {"input_tokens": 0, "output_tokens": 0})

    def test_missing_literal_fields_do_not_add_blank_lines(self):
        merged = merge_process_maps(
            [
                (0, {"title": "Uno", "transcript": "[00:01] alfa"}),
                (900, {"title": "Due"}),
            ]
        )
        self.assertEqual(merged["transcript"], "[00:01] alfa")


if __name__ == "__main__":
    unittest.main()
