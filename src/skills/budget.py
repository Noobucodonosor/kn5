"""
BudgetTracker: tiene il conto del costo Gemini stimato per mese e blocca le
chiamate quando si supera il tetto.

Persistenza: `library/.budget.json` con shape
    {
        "2026-06": {
            "spent_usd": 1.23,
            "calls": 5,
            "input_tokens": 10000,
            "output_tokens": 5000,
            "events": [
                {"ts": "2026-06-17T18:30", "in": 5000, "out": 2500, "usd": 0.62, "model": "gemini-2.5-flash", "label": "reel C123"}
            ]
        }
    }

Il costo è stimato (la fattura reale GCP arriva con 24-48h di ritardo). Il
tracker è una sentinella, non un rendicontatore: blocca prima di superare
la soglia, ma non sostituisce il monitoraggio del billing su GCP.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Union

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


# Prezzi Gemini 2.5 Flash (USD per 1M token). Sono override-abili via env per
# coprire cambi di pricing o passaggio a Pro senza dover modificare il codice.
DEFAULT_INPUT_USD_PER_1M = 0.30
DEFAULT_OUTPUT_USD_PER_1M = 2.50
DEFAULT_MONTHLY_BUDGET_USD = 20.0

# Limite massimo di eventi salvati per mese: oltre, troncati per non far
# crescere il file all'infinito (la somma rimane fedele, solo lo storico cala).
MAX_EVENTS_PER_MONTH = 500


class BudgetExceeded(Exception):
    """Sollevata quando un check_or_raise() trova lo stato già sopra soglia."""


class BudgetTracker:
    def __init__(
        self,
        budget_file: Union[str, Path] = "library/.budget.json",
        env_path: Union[str, Path] = ".env",
    ):
        load_dotenv(env_path)
        self.budget_file = Path(budget_file)
        self.budget_file.parent.mkdir(parents=True, exist_ok=True)

        self.input_usd_per_1m = float(
            os.getenv("GEMINI_INPUT_USD_PER_1M", DEFAULT_INPUT_USD_PER_1M)
        )
        self.output_usd_per_1m = float(
            os.getenv("GEMINI_OUTPUT_USD_PER_1M", DEFAULT_OUTPUT_USD_PER_1M)
        )
        self.monthly_limit_usd = float(
            os.getenv("MONTHLY_BUDGET_USD", DEFAULT_MONTHLY_BUDGET_USD)
        )

        self._data: Dict = self._load()

    # ---------- IO ----------

    def _load(self) -> Dict:
        if not self.budget_file.exists():
            return {}
        try:
            with open(self.budget_file, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Budget file corrotto, reset: %s", e)
            return {}

    def _save(self) -> None:
        try:
            with open(self.budget_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except OSError as e:
            logger.warning("Impossibile salvare budget: %s", e)

    # ---------- Costo stimato ----------

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_usd_per_1m
            + output_tokens / 1_000_000 * self.output_usd_per_1m
        )

    # ---------- Mutazione ----------

    @staticmethod
    def _current_month() -> str:
        return datetime.now().strftime("%Y-%m")

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M")

    def record(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str = "gemini-2.5-flash",
        label: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> float:
        """Registra una chiamata una sola volta per ``event_id``."""
        cost = self.estimate_cost(input_tokens, output_tokens)
        month = self._current_month()
        bucket = self._data.setdefault(
            month,
            {"spent_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "events": []},
        )
        if event_id:
            previous = next(
                (event for event in bucket["events"] if event.get("event_id") == event_id),
                None,
            )
            if previous is not None:
                return float(previous["usd"])
        bucket["spent_usd"] = round(bucket["spent_usd"] + cost, 6)
        bucket["calls"] += 1
        bucket["input_tokens"] += int(input_tokens)
        bucket["output_tokens"] += int(output_tokens)
        event = {
            "ts": self._now_iso(),
            "in": int(input_tokens),
            "out": int(output_tokens),
            "usd": round(cost, 6),
            "model": model,
        }
        if label:
            event["label"] = label[:120]
        if event_id:
            event["event_id"] = event_id[:120]
        bucket["events"].append(event)
        # Tronca lo storico se eccessivo (mantieni i più recenti)
        if len(bucket["events"]) > MAX_EVENTS_PER_MONTH:
            bucket["events"] = bucket["events"][-MAX_EVENTS_PER_MONTH:]
        self._save()
        return cost

    # ---------- Read ----------

    def status(self, month: Optional[str] = None) -> Dict:
        """
        Stato budget per il mese corrente (o specificato).

        Returns:
            {
                "month": "2026-06",
                "limit_usd": 20.0,
                "spent_usd": 3.42,
                "remaining_usd": 16.58,
                "percent": 17.1,
                "calls": 12,
                "input_tokens": 35000,
                "output_tokens": 12000,
                "exceeded": False,
                "warning": False,   # True a >= 80%
            }
        """
        month = month or self._current_month()
        bucket = self._data.get(month, {})
        spent = float(bucket.get("spent_usd", 0.0))
        percent = (spent / self.monthly_limit_usd * 100) if self.monthly_limit_usd > 0 else 0.0
        return {
            "month": month,
            "limit_usd": round(self.monthly_limit_usd, 2),
            "spent_usd": round(spent, 4),
            "remaining_usd": round(max(0.0, self.monthly_limit_usd - spent), 4),
            "percent": round(percent, 1),
            "calls": int(bucket.get("calls", 0)),
            "input_tokens": int(bucket.get("input_tokens", 0)),
            "output_tokens": int(bucket.get("output_tokens", 0)),
            "exceeded": spent >= self.monthly_limit_usd,
            "warning": percent >= 80.0,
        }

    def history(self, last_n_events: int = 20) -> list:
        """Ultimi N eventi del mese corrente, per il widget dashboard."""
        bucket = self._data.get(self._current_month(), {})
        events = bucket.get("events", [])
        return list(reversed(events[-last_n_events:]))

    # ---------- Gate ----------

    def check_or_raise(self) -> None:
        """Solleva BudgetExceeded se siamo già sopra soglia."""
        st = self.status()
        if st["exceeded"]:
            raise BudgetExceeded(
                f"Budget mensile superato: ${st['spent_usd']:.2f} / ${st['limit_usd']:.2f} "
                f"({st['percent']}%) per {st['month']}. "
                f"Aspetta il prossimo mese o alza MONTHLY_BUDGET_USD."
            )

    def can_afford(self, estimated_cost_usd: float) -> bool:
        """True se la prossima chiamata stimata sta dentro al cap."""
        st = self.status()
        return (st["spent_usd"] + estimated_cost_usd) <= self.monthly_limit_usd
