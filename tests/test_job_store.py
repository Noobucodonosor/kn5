import json
import unittest

from src.skills.job_store import JobStore


class FakeBlob:
    def __init__(self):
        self.value = None
        self.content_type = None

    def exists(self):
        return self.value is not None

    def upload_from_string(self, value, content_type=None):
        self.value = value
        self.content_type = content_type

    def download_as_text(self):
        return self.value


class FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, name):
        return self.blobs.setdefault(name, FakeBlob())


class JobStoreTests(unittest.TestCase):
    def setUp(self):
        self.bucket = FakeBucket()
        self.store = JobStore(bucket=self.bucket)

    def test_job_round_trip(self):
        self.store.write({"id": "abc", "status": "queued"})
        loaded = self.store.read("abc")
        self.assertEqual(loaded["status"], "queued")
        self.assertIn("updated_at", loaded)

    def test_segment_checkpoint_round_trip(self):
        process_map = {"title": "Parte uno", "transcript": "[00:01] testo"}
        self.store.write_segment("abc", 900, process_map)
        self.assertEqual(self.store.read_segment("abc", 900), process_map)
        self.assertIsNone(self.store.read_segment("abc", 0))

    def test_rejects_checkpoint_with_wrong_offset(self):
        blob = self.bucket.blob("jobs/abc/segments/00000900.json")
        blob.upload_from_string(json.dumps({"offset_seconds": 0, "process_map": {"title": "errato"}}))
        self.assertIsNone(self.store.read_segment("abc", 900))

    def test_artifact_round_trip_and_allowlist(self):
        self.store.write_artifacts(
            "abc",
            {"document": "# Documento", "manifest": '{"schema_version":"1.0"}'},
        )
        document = self.store.read_artifact("abc", "document")
        self.assertEqual(document[0], "# Documento")
        self.assertEqual(document[1], "text/markdown; charset=utf-8")
        self.assertIsNone(self.store.read_artifact("abc", "../../secret"))

    def test_rejects_unknown_artifact_on_write(self):
        with self.assertRaises(ValueError):
            self.store.write_artifacts("abc", {"unknown": "contenuto"})


if __name__ == "__main__":
    unittest.main()
