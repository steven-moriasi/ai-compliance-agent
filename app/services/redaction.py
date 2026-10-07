"""Strip personal data before a model call or a log line.

Placeholders are short bracket markers. They do not form retrieval tokens, and
retrieval keeps using the original document.
"""

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_SSN = re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")
_PHONE = re.compile(r"(?<!\d)(?:\+1[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\d)")
_CARD = re.compile(r"(?<!\d)\d(?:[ -]?\d){12,18}(?!\d)")

_installed = False
_previous_factory = logging.getLogRecordFactory()
_previous_format_exception = logging.Formatter.formatException


@dataclass(frozen=True)
class Redaction:
    text: str
    count: int


def redact_personal_data(text: str) -> Redaction:
    """Replace emails, card numbers, SSNs, and phone numbers with short markers."""
    redacted, emails = _replace(_EMAIL, text, "[e]")
    redacted, cards = _replace(_CARD, redacted, "[c]", _is_payment_card)
    redacted, ssns = _replace(_SSN, redacted, "[s]")
    redacted, phones = _replace(_PHONE, redacted, "[p]")
    return Redaction(redacted, emails + cards + ssns + phones)


def install_log_redaction() -> None:
    """Redact personal data in every log record and formatted traceback."""
    global _installed
    if _installed:
        return
    logging.setLogRecordFactory(_redacted_record)
    logging.Formatter.formatException = _redacted_exception  # type: ignore[method-assign]
    _installed = True


def _replace(
    pattern: re.Pattern[str],
    text: str,
    placeholder: str,
    accept: Callable[[re.Match[str]], bool] | None = None,
) -> tuple[str, int]:
    count = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal count
        if accept is not None and not accept(match):
            return match.group(0)
        count += 1
        return placeholder

    return pattern.sub(replace, text), count


def _is_payment_card(match: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", match.group(0))
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, character in enumerate(reversed(digits)):
        value = int(character)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redacted_record(*args: object, **kwargs: object) -> logging.LogRecord:
    record = _previous_factory(*args, **kwargs)
    try:
        rendered = record.getMessage()
    except (TypeError, ValueError):
        return record
    cleaned = redact_personal_data(rendered).text
    if cleaned != rendered:
        record.msg = cleaned
        record.args = ()
    return record


def _redacted_exception(self: logging.Formatter, ei: object) -> str:
    rendered = _previous_format_exception(self, ei)  # type: ignore[arg-type]
    return redact_personal_data(rendered).text
