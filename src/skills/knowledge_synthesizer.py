"""
KnowledgeSynthesizer: integrazione con Google Vertex AI (Gemini) per l'analisi
multimodale di video e immagini.

Output: schema Pydantic ProcessMap che combina interpretazione (title, summary,
steps, insights) e fedeltà letterale (transcript verbatim, OCR on-screen,
descrizione visiva). Usato dal library_writer per generare markdown denso.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Union

from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.cloud import storage
from pydantic import ValidationError
from src.skills.content_schema import ProcessMap
from src.skills.retry import call_with_retry

logger = logging.getLogger(__name__)


# =====================================================================
# Eccezioni
# =====================================================================


class KnowledgeSynthesizerError(Exception):
    """Eccezione base."""


class GCSError(KnowledgeSynthesizerError):
    """Errore su Google Cloud Storage."""


class GeminiAPIError(KnowledgeSynthesizerError):
    """Errore sulla chiamata Gemini."""


# =====================================================================
# Synthesizer
# =====================================================================


SYSTEM_INSTRUCTION = (
    "Sei un analista esperto nella conversione di contenuti multimediali in testo. "
    "Il tuo compito è triplice:\n"
    "1) RENDERE IL CONTENUTO UTILIZZABILE: identifica content_kind e scrivi in "
    "textual_document un equivalente testuale completo, non una nota o una semplice sintesi.\n"
    "2) INTERPRETARE: estrai title, executive_summary, steps quando esistono davvero, "
    "epistemic_insights e tags.\n"
    "3) PRESERVARE FEDELMENTE: trascrivi VERBATIM l'audio (transcript) e il testo "
    "on-screen (ocr_text), senza parafrasi né riassunti. La descrizione visiva "
    "(visual_description) può essere sintetica.\n\n"
    "Regole rigide:\n"
    "- transcript = solo parole pronunciate, parola per parola.\n"
    "- ocr_text = solo testo visibile a schermo, letterale.\n"
    "- Se il contenuto non descrive un processo sequenziale, lascia steps = [].\n"
    "- Un tutorial deve diventare un tutorial scritto eseguibile, con prerequisiti, "
    "passaggi, esempi, avvertenze e risultato quando presenti.\n"
    "- Un'intervista conserva domande, risposte e posizioni degli interlocutori; una "
    "spiegazione conserva tesi, ragionamento ed evidenze; una narrazione conserva ordine e nessi.\n"
    "- textual_document usa solo informazioni sostenute da audio, testo on-screen o immagini.\n"
    "- Mantieni la lingua originale dei verbatim; sintesi e tag in italiano."
)

USER_PROMPT_VIDEO = (
    "Analizza video + audio. Produci l'equivalente testuale completo adattato al genere, "
    "l'interpretazione strutturata e le fonti letterali audio/on-screen richieste dallo schema JSON."
)

USER_PROMPT_IMAGES = (
    "Questo è un carosello di immagini (o singola immagine). Analizza ordine, testo presente, "
    "elementi visivi. Trascrivi VERBATIM tutto il testo on-screen di ogni slide nel campo ocr_text. "
    "Ricostruisci in textual_document un equivalente testuale completo che conservi la sequenza "
    "e il fine del carosello. Niente audio = transcript vuoto. Compila tutti gli altri campi."
)


class KnowledgeSynthesizer:
    """
    Manda media (video o lista immagini) a Google Gemini su Vertex AI.
    Restituisce un dict conforme allo schema ProcessMap + metadata di usage
    (input_tokens, output_tokens) per il budget tracker.
    """

    def __init__(self, env_path: Union[str, Path] = ".env"):
        load_dotenv(env_path)

        self.project_id = os.getenv("GCP_PROJECT", "").strip()
        self.location = os.getenv("GCP_LOCATION", "europe-west9").strip()
        self.bucket_name = os.getenv("GCS_BUCKET", "").strip()
        self.model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
        try:
            self.gemini_max_attempts = int(os.getenv("GEMINI_MAX_ATTEMPTS", "4"))
            self.gemini_retry_base_seconds = float(
                os.getenv("GEMINI_RETRY_BASE_SECONDS", "2")
            )
            if self.gemini_max_attempts < 1 or self.gemini_retry_base_seconds < 0:
                raise ValueError
        except ValueError as exc:
            raise KnowledgeSynthesizerError(
                "GEMINI_MAX_ATTEMPTS deve essere >= 1 e "
                "GEMINI_RETRY_BASE_SECONDS deve essere >= 0"
            ) from exc

        missing = [
            name
            for name, value in (("GCP_PROJECT", self.project_id), ("GCS_BUCKET", self.bucket_name))
            if not value
        ]
        if missing:
            raise KnowledgeSynthesizerError(
                "Config Vertex AI incompleta: imposta "
                + ", ".join(missing)
                + " nel file .env (vedi .env.example)."
            )

        logger.info(
            "Init Gemini Vertex AI: project=%s model=%s", self.project_id, self.model_name
        )

        try:
            self._client = genai.Client(
                vertexai=True, project=self.project_id, location=self.location
            )
            self._storage_client = storage.Client(project=self.project_id)
        except Exception as e:
            raise KnowledgeSynthesizerError(
                f"Init client Google Cloud fallito (credenziali?): {e}"
            ) from e

    # ---------- GCS helpers (video upload) ----------

    def _upload_to_gcs(self, local_path: Path) -> str:
        try:
            bucket = self._storage_client.bucket(self.bucket_name)
            blob_name = f"tmp_gemini_processing/{local_path.name}"
            blob = bucket.blob(blob_name)
            logger.info("Upload GCS: %s -> gs://%s/%s", local_path.name, self.bucket_name, blob_name)
            blob.upload_from_filename(str(local_path))
            return f"gs://{self.bucket_name}/{blob_name}"
        except Exception as e:
            raise GCSError(f"Upload GCS fallito ({local_path.name}): {e}") from e

    def _delete_from_gcs(self, gcs_uri: str) -> None:
        try:
            if not gcs_uri.startswith("gs://"):
                return
            bucket_name, blob_name = gcs_uri[5:].split("/", 1)
            bucket = self._storage_client.bucket(bucket_name)
            bucket.blob(blob_name).delete()
            logger.info("Cleanup GCS: %s", gcs_uri)
        except Exception as e:
            logger.warning("Cleanup GCS fallito (%s): %s", gcs_uri, e)

    # ---------- Image inline part ----------

    @staticmethod
    def _load_image_part(image_path: Path) -> types.Part:
        suffix = image_path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }
        mime_type = mime_map.get(suffix, "image/jpeg")
        with open(image_path, "rb") as f:
            data = f.read()
        return types.Part.from_bytes(data=data, mime_type=mime_type)

    # ---------- Output parsing ----------

    @staticmethod
    def _usage_from_response(response) -> Dict[str, int]:
        """Estrae input/output tokens da response.usage_metadata per il budget tracker."""
        usage = getattr(response, "usage_metadata", None)
        if not usage:
            return {"input_tokens": 0, "output_tokens": 0}
        return {
            "input_tokens": getattr(usage, "prompt_token_count", 0) or 0,
            "output_tokens": getattr(usage, "candidates_token_count", 0) or 0,
        }

    def _parse_response(self, response) -> Dict:
        try:
            data = ProcessMap.model_validate_json(response.text).model_dump()
        except (ValidationError, AttributeError) as e:
            raise GeminiAPIError(f"Risposta Gemini non conforme al contratto: {e}") from e
        data["_usage"] = self._usage_from_response(response)
        return data

    def _generate_content(self, **kwargs):
        """Invoca Gemini ritentando solo rate limit e guasti server temporanei."""

        def on_retry(error: Exception, attempt: int, delay: float) -> None:
            logger.warning(
                "Gemini temporaneamente non disponibile (%s). "
                "Tentativo %d/%d fallito, nuovo tentativo fra %.1fs",
                error,
                attempt,
                self.gemini_max_attempts,
                delay,
            )

        return call_with_retry(
            lambda: self._client.models.generate_content(**kwargs),
            max_attempts=self.gemini_max_attempts,
            base_delay_seconds=self.gemini_retry_base_seconds,
            on_retry=on_retry,
        )

    # ---------- Public API ----------

    def generate_from_video(self, video_path: Union[str, Path]) -> Dict:
        """
        Carica il video su GCS, invoca Gemini, restituisce ProcessMap dict + _usage.
        Pulisce sempre il blob GCS in finally.
        """
        video_path = Path(video_path)
        if not video_path.exists():
            raise KnowledgeSynthesizerError(f"Video non trovato: {video_path}")

        suffix = video_path.suffix.lower()
        mime_type = {
            ".mp4": "video/mp4",
            ".mov": "video/quicktime",
            ".webm": "video/webm",
        }.get(suffix, "video/mp4")

        gcs_uri: Optional[str] = None
        try:
            gcs_uri = self._upload_to_gcs(video_path)
            logger.info("Gemini (%s) — analisi video", self.model_name)
            response = self._generate_content(
                model=self.model_name,
                contents=[
                    types.Part.from_uri(file_uri=gcs_uri, mime_type=mime_type),
                    USER_PROMPT_VIDEO,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ProcessMap,
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                ),
            )
            return self._parse_response(response)
        except (GCSError, GeminiAPIError):
            raise
        except Exception as e:
            logger.error("Errore Gemini su video: %s", e, exc_info=True)
            raise GeminiAPIError(f"Errore Gemini: {e}") from e
        finally:
            if gcs_uri:
                self._delete_from_gcs(gcs_uri)

    # Estensioni immagine accettate da Gemini (i caroselli misti includono anche
    # .mp4: vanno esclusi qui, altrimenti Gemini risponde 400 "image not valid").
    _IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

    def generate_from_images(self, image_paths: List[Union[str, Path]]) -> Dict:
        """
        Manda lista immagini inline a Gemini, restituisce ProcessMap dict + _usage.
        Ignora gli asset non-immagine (es. la slide-video di un carosello misto).
        """
        if not image_paths:
            raise KnowledgeSynthesizerError("Nessuna immagine fornita")

        try:
            contents: List = []
            for p in image_paths:
                path = Path(p)
                if not path.exists():
                    continue
                if path.suffix.lower() not in self._IMAGE_EXTS:
                    logger.info("Salto asset non-immagine per Gemini: %s", path.name)
                    continue
                contents.append(self._load_image_part(path))
            if not contents:
                raise KnowledgeSynthesizerError("Nessuna immagine valida da caricare")
            contents.append(USER_PROMPT_IMAGES)

            logger.info("Gemini (%s) — analisi %d immagini", self.model_name, len(contents) - 1)
            response = self._generate_content(
                model=self.model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ProcessMap,
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.2,
                ),
            )
            return self._parse_response(response)
        except GeminiAPIError:
            raise
        except Exception as e:
            logger.error("Errore Gemini su immagini: %s", e, exc_info=True)
            raise GeminiAPIError(f"Errore Gemini: {e}") from e

    # ---------- Retro-compatibilità ----------
    # Le entry esistenti (main.py, telegram_bot.py, bulk_processor.py) chiamano
    # i metodi legacy. Mantieni gli alias finché non vengono migrati.

    def generate_process_map_from_video(self, video_path: Union[str, Path]) -> Dict:
        return self.generate_from_video(video_path)

    def generate_process_map_from_images(self, image_paths: List[Union[str, Path]]) -> Dict:
        return self.generate_from_images(image_paths)
