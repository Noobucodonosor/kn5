"""API asincrona per i job web KN5.

I job sono persistiti su GCS, avviati tramite Cloud Tasks e processati dal
motore KN5. I video lunghi vengono divisi in segmenti e ricomposti senza
chiedere al modello di riassumere nuovamente il risultato.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request
from google.cloud import tasks_v2

from src.skills.budget import BudgetTracker
from src.skills.job_store import JobStore
from src.skills.knowledge_synthesizer import KnowledgeSynthesizer
from src.skills.library_writer import LibraryWriter
from src.skills.media_fetcher import MediaFetcher
from src.skills.process_map import merge_process_maps
from src.skills.retry import is_transient_error
from src.skills.verification import verify_process_map

logger = logging.getLogger("job_api")
jobs_bp = Blueprint("jobs", __name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bearer_token() -> str:
    value = request.headers.get("Authorization", "")
    return value[7:] if value.startswith("Bearer ") else ""


def _authorized() -> bool:
    expected = os.getenv("KN5_API_TOKEN", "")
    return bool(expected) and _bearer_token() == expected


def _worker_authorized() -> bool:
    expected = os.getenv("JOB_WORKER_TOKEN", "")
    return bool(expected) and request.headers.get("X-KN5-Worker-Token", "") == expected


def _enqueue(job_id: str) -> None:
    project = os.environ["GCP_PROJECT"]
    location = os.getenv("CLOUD_TASKS_LOCATION", os.getenv("GCP_LOCATION", "us-central1"))
    queue = os.getenv("CLOUD_TASKS_QUEUE", "kn5-jobs")
    service_url = os.environ["WEBHOOK_URL"].rstrip("/")
    worker_token = os.environ["JOB_WORKER_TOKEN"]
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project, location, queue)
    task = {
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{service_url}/api/jobs/{job_id}/run",
            "headers": {"X-KN5-Worker-Token": worker_token},
        },
        "dispatch_deadline": {"seconds": 1800},
    }
    client.create_task(parent=parent, task=task)


def _base_stages() -> list[dict]:
    return [
        {"key": "acquire", "label": "Acquisizione", "status": "pending"},
        {"key": "segment", "label": "Segmentazione", "status": "pending"},
        {"key": "analyze", "label": "Audio + OCR", "status": "pending"},
        {"key": "verify", "label": "Verifica", "status": "pending"},
        {"key": "document", "label": "Pacchetto testuale", "status": "pending"},
    ]


def _set_stage(job: dict, store: JobStore, key: str, status: str, percent: int, detail: str = ""):
    for stage in job["stages"]:
        if stage["key"] == key:
            stage["status"] = status
            stage["detail"] = detail
        elif stage["status"] == "running" and stage["key"] != key:
            stage["status"] = "complete"
    job.update({"stage": key, "status": "running", "percent": percent, "message": detail})
    store.write(job)


@jobs_bp.route("/api/jobs", methods=["POST"])
def create_job():
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    url = str((request.json or {}).get("url", "")).strip()
    if not re.match(r"^https://(www\.)?(youtube\.com|youtu\.be|instagram\.com|instagr\.am)/", url):
        return jsonify({"error": "URL YouTube o Instagram non valido"}), 400
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "url": url,
        "status": "queued",
        "stage": "queued",
        "percent": 0,
        "message": "In coda",
        "created_at": _now(),
        "updated_at": _now(),
        "stages": _base_stages(),
        "segments": {"total": 0, "complete": 0},
        "result_available": False,
    }
    store = JobStore()
    store.write(job)
    try:
        _enqueue(job_id)
    except Exception as exc:
        logger.error("Impossibile accodare job %s: %s", job_id, exc, exc_info=True)
        job.update({"status": "failed", "message": "Accodamento fallito", "error": str(exc)})
        store.write(job)
        return jsonify(job), 503
    return jsonify(job), 202


@jobs_bp.route("/api/jobs/<job_id>", methods=["GET"])
def get_job(job_id: str):
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    job = JobStore().read(job_id)
    return (jsonify(job), 200) if job else (jsonify({"error": "not found"}), 404)


@jobs_bp.route("/api/jobs/<job_id>/result", methods=["GET"])
def get_job_result(job_id: str):
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    store = JobStore()
    artifact = store.read_artifact(job_id, "document")
    result = artifact[0] if artifact else store.read_result(job_id)
    return Response(result, mimetype="text/markdown") if result else (jsonify({"error": "not ready"}), 404)


@jobs_bp.route("/api/jobs/<job_id>/artifacts/<name>", methods=["GET"])
def get_job_artifact(job_id: str, name: str):
    if not _authorized():
        return jsonify({"error": "unauthorized"}), 401
    artifact = JobStore().read_artifact(job_id, name)
    if artifact is None:
        return jsonify({"error": "artifact not found"}), 404
    content, content_type = artifact
    return Response(content, content_type=content_type)


def _duration(path: Path) -> float:
    output = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        text=True,
    )
    return float(output.strip())


def _split_video(path: Path, out_dir: Path, segment_seconds: int) -> list[tuple[int, Path]]:
    duration = _duration(path)
    if duration <= segment_seconds:
        return [(0, path)]
    segments: list[tuple[int, Path]] = []
    for start in range(0, int(duration) + 1, segment_seconds):
        if start >= duration:
            break
        output = out_dir / f"segment_{start:06d}.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(start), "-i", str(path), "-t", str(min(segment_seconds, duration-start)),
             "-c", "copy", "-avoid_negative_ts", "make_zero", str(output)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        segments.append((start, output))
    return segments


@jobs_bp.route("/api/jobs/<job_id>/run", methods=["POST"])
def run_job(job_id: str):
    if not _worker_authorized():
        return jsonify({"error": "unauthorized"}), 401
    store = JobStore()
    job = store.read(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    if job["status"] == "complete":
        return jsonify(job)

    work_dir = Path(tempfile.mkdtemp(prefix=f"kn5_job_{job_id}_"))
    try:
        job.pop("error", None)
        BudgetTracker().check_or_raise()
        _set_stage(job, store, "acquire", "running", 5, "Download del contenuto e lettura metadata")
        fetcher = MediaFetcher(temp_dir=work_dir / "media", cache_file=work_dir / "cache.json")
        result = fetcher.fetch(job["url"])
        acquisition_cost = float(result.get("acquisition_cost_usd", 0) or 0)
        metadata = fetcher.extract_metadata(job["url"])
        metadata["media_type"] = result.get("media_type", "video")
        assets = result["assets"]

        synthesizer = KnowledgeSynthesizer()
        if metadata["media_type"] == "video":
            _set_stage(job, store, "segment", "running", 15, "Misurazione e segmentazione del video")
            segment_seconds = int(
                job.get("segment_seconds") or os.getenv("LONG_VIDEO_SEGMENT_SEC", "900")
            )
            job["segment_seconds"] = segment_seconds
            parts = _split_video(Path(assets[0]), work_dir, segment_seconds)
            job["segments"] = {"total": len(parts), "complete": 0}
            store.write(job)
            analyzed = []
            for index, (offset, segment) in enumerate(parts, 1):
                cached = store.read_segment(job_id, offset)
                if cached is not None:
                    analyzed.append((offset, cached))
                    job["segments"]["complete"] = len(analyzed)
                    job["message"] = f"Segmento {index}/{len(parts)} recuperato dal checkpoint"
                    store.write(job)
                    continue
                percent = 20 + int(55 * (index - 1) / max(1, len(parts)))
                _set_stage(job, store, "analyze", "running", percent, f"Segmento {index}/{len(parts)} · audio, video e OCR")
                process_map = synthesizer.generate_from_video(segment)
                store.write_segment(job_id, offset, process_map)
                analyzed.append((offset, process_map))
                job["segments"]["complete"] = len(analyzed)
                store.write(job)
            pm = merge_process_maps(analyzed)
            source_maps = [part for _, part in analyzed]
        else:
            job["segments"] = {"total": 1, "complete": 0}; store.write(job)
            _set_stage(job, store, "analyze", "running", 30, "Analisi immagini e OCR")
            pm = store.read_segment(job_id, 0)
            if pm is None:
                pm = synthesizer.generate_from_images(assets)
                store.write_segment(job_id, 0, pm)
            job["segments"]["complete"] = 1; store.write(job)
            source_maps = [pm]

        _set_stage(job, store, "verify", "running", 82, "Controllo strutturale delle evidenze estratte")
        verification = verify_process_map(
            pm,
            media_type=metadata["media_type"],
            expected_segments=job["segments"]["total"],
            segment_maps=source_maps,
        )

        _set_stage(job, store, "document", "running", 94, "Composizione dei quattro artefatti testuali")
        usage = pm.pop("_usage", {"input_tokens": 0, "output_tokens": 0})
        cost = BudgetTracker().record(
            usage["input_tokens"],
            usage["output_tokens"],
            synthesizer.model_name,
            pm.get("title") or job["url"],
            event_id=job_id,
        )
        artifact_paths = LibraryWriter().write_artifact_package(
            pm,
            metadata,
            verification,
            cost_usd=cost,
            generator_model=synthesizer.model_name,
        )
        artifact_contents = {
            name: path.read_text(encoding="utf-8")
            for name, path in artifact_paths.items()
        }
        store.write_artifacts(job_id, artifact_contents)
        # Endpoint legacy: continua a restituire il documento principale.
        store.result(job_id, artifact_contents["document"])
        for stage in job["stages"]:
            if stage["status"] in {"running", "pending"}:
                stage["status"] = "complete"
        job.update({"status": "complete", "stage": "complete", "percent": 100,
                    "message": "Documento KN5 pronto", "result_available": True,
                    "verification": verification, "cost_usd": round(cost + acquisition_cost, 6),
                    "title": pm.get("title"),
                    "artifacts": {name: path.name for name, path in artifact_paths.items()}})
        store.write(job)
        return jsonify(job)
    except Exception as exc:
        logger.error("Job %s fallito: %s", job_id, exc, exc_info=True)
        if is_transient_error(exc):
            job.update({
                "status": "queued",
                "message": "Servizio temporaneamente non disponibile: nuovo tentativo in coda",
                "error": str(exc),
            })
            store.write(job)
            # Cloud Tasks ritenta le risposte non-2xx. I checkpoint evitano di
            # rianalizzare e ripagare i segmenti già completati.
            return jsonify(job), 503
        job.update({"status": "failed", "message": "Elaborazione fallita", "error": str(exc)})
        store.write(job)
        # Un errore permanente è gestito con 200 per evitare retry infiniti.
        return jsonify(job), 200
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
