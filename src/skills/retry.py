"""Retry deterministico per errori temporanei di servizi HTTP esterni."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
_STATUS_IN_MESSAGE = re.compile(r"(?:^|\s)(429|50[0234])(?:\s|:|$)")


def error_status_code(error: Exception) -> int | None:
    """Ricava uno status HTTP senza dipendere dall'SDK che ha creato l'errore."""

    visited: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        for attribute in ("status_code", "code"):
            value = getattr(current, attribute, None)
            if callable(value):
                value = value()
            try:
                return int(value)
            except (TypeError, ValueError):
                pass

        match = _STATUS_IN_MESSAGE.search(str(current))
        if match:
            return int(match.group(1))
        current = current.__cause__ or current.__context__
    return None


def is_transient_error(error: Exception) -> bool:
    """True solo per rate limit e guasti server normalmente recuperabili."""

    return error_status_code(error) in TRANSIENT_STATUS_CODES


def call_with_retry(
    operation: Callable[[], T],
    *,
    max_attempts: int = 4,
    base_delay_seconds: float = 2.0,
    max_delay_seconds: float = 30.0,
    should_retry: Callable[[Exception], bool] = is_transient_error,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[Exception, int, float], None] | None = None,
) -> T:
    """Esegue ``operation`` con backoff esponenziale per errori temporanei.

    ``on_retry`` riceve l'errore, il numero del tentativo appena fallito e
    l'attesa prevista. L'ultimo errore viene sempre rilanciato invariato.
    """

    if max_attempts < 1:
        raise ValueError("max_attempts deve essere almeno 1")
    if base_delay_seconds < 0 or max_delay_seconds < 0:
        raise ValueError("i ritardi non possono essere negativi")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as error:
            if attempt >= max_attempts or not should_retry(error):
                raise
            delay = min(base_delay_seconds * (2 ** (attempt - 1)), max_delay_seconds)
            if on_retry:
                on_retry(error, attempt, delay)
            sleep(delay)

    raise RuntimeError("retry loop terminato senza risultato")  # pragma: no cover
