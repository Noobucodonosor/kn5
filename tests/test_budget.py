import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.skills.budget import BudgetTracker


class BudgetTrackerTests(unittest.TestCase):
    def test_event_id_makes_recording_idempotent(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            "os.environ",
            {
                "GEMINI_INPUT_USD_PER_1M": "1",
                "GEMINI_OUTPUT_USD_PER_1M": "1",
                "MONTHLY_BUDGET_USD": "20",
            },
        ):
            tracker = BudgetTracker(budget_file=Path(directory) / "budget.json")
            first = tracker.record(1000, 500, event_id="job-123")
            second = tracker.record(1000, 500, event_id="job-123")

            self.assertEqual(first, second)
            self.assertEqual(tracker.status()["calls"], 1)
            self.assertEqual(len(tracker.history()), 1)


if __name__ == "__main__":
    unittest.main()
