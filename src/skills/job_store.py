"""Persistenza GCS dei job asincroni e dei checkpoint di segmento."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """Archivio dei job; ``bucket`` è iniettabile per test offline."""

    ARTIFACT_FILES = {
        "document": ("document.md", "text/markdown; charset=utf-8"),
        "transcript": ("transcript.md", "text/markdown; charset=utf-8"),
        "visual": ("visual.md", "text/markdown; charset=utf-8"),
        "manifest": ("manifest.json", "application/json; charset=utf-8"),
    }

    def __init__(self, bucket=None):
        if bucket is None:
            from google.cloud import storage

            project = os.environ["GCP_PROJECT"]
            bucket_name = os.environ["GCS_BUCKET"]
            bucket = storage.Client(project=project).bucket(bucket_name)
        self.bucket = bucket

    def _blob(self, job_id: str):
        return self.bucket.blob(f"jobs/{job_id}/status.json")

    def _segment_blob(self, job_id: str, offset_seconds: int):
        return self.bucket.blob(f"jobs/{job_id}/segments/{offset_seconds:08d}.json")

    def read(self, job_id: str) -> dict | None:
        blob = self._blob(job_id)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())

    def write(self, job: dict) -> dict:
        job["updated_at"] = _now()
        self._blob(job["id"]).upload_from_string(
            json.dumps(job, ensure_ascii=False),
            content_type="application/json",
        )
        return job

    def result(self, job_id: str, markdown: str) -> None:
        self.bucket.blob(f"jobs/{job_id}/result.md").upload_from_string(
            markdown,
            content_type="text/markdown; charset=utf-8",
        )

    def read_result(self, job_id: str) -> str | None:
        blob = self.bucket.blob(f"jobs/{job_id}/result.md")
        return blob.download_as_text() if blob.exists() else None

    def write_artifacts(self, job_id: str, artifacts: dict[str, str]) -> None:
        """Salva soltanto gli artefatti previsti dal contratto pubblico."""

        unknown = set(artifacts) - set(self.ARTIFACT_FILES)
        if unknown:
            raise ValueError(f"Artefatti non supportati: {', '.join(sorted(unknown))}")
        for name, content in artifacts.items():
            filename, content_type = self.ARTIFACT_FILES[name]
            self.bucket.blob(f"jobs/{job_id}/artifacts/{filename}").upload_from_string(
                content,
                content_type=content_type,
            )

    def read_artifact(self, job_id: str, name: str) -> tuple[str, str] | None:
        """Legge un artefatto consentito e restituisce contenuto e MIME type."""

        artifact = self.ARTIFACT_FILES.get(name)
        if artifact is None:
            return None
        filename, content_type = artifact
        blob = self.bucket.blob(f"jobs/{job_id}/artifacts/{filename}")
        if not blob.exists():
            return None
        return blob.download_as_text(), content_type

    def write_segment(self, job_id: str, offset_seconds: int, process_map: dict) -> None:
        """Salva un'analisi completa prima di avanzare al segmento successivo."""

        payload = {"offset_seconds": offset_seconds, "process_map": process_map}
        self._segment_blob(job_id, offset_seconds).upload_from_string(
            json.dumps(payload, ensure_ascii=False),
            content_type="application/json",
        )

    def read_segment(self, job_id: str, offset_seconds: int) -> dict | None:
        """Restituisce il ProcessMap già calcolato, se il checkpoint esiste."""

        blob = self._segment_blob(job_id, offset_seconds)
        if not blob.exists():
            return None
        payload = json.loads(blob.download_as_text())
        if payload.get("offset_seconds") != offset_seconds:
            return None
        process_map = payload.get("process_map")
        return process_map if isinstance(process_map, dict) else None
