"""Turn Federal Register markup into searchable plain text.

Subscript tags are rewritten before the remaining tags are removed. Doing it in
the other order would delete the chemical index from NOx and PM2.5.
"""

import re
import unicodedata

SUBSCRIPT_PATTERNS = (
    re.compile(
        r"""<E\b[^>]*\bT\s*=\s*(['"])52\1[^>]*>(.*?)</E>""",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"<sub\b[^>]*>(.*?)</sub>", re.IGNORECASE | re.DOTALL),
    re.compile(r"<inf\b[^>]*>(.*?)</inf>", re.IGNORECASE | re.DOTALL),
)
TAG_PATTERN = re.compile(r"</?[^>]+>")
RESIDUAL_TAG_PATTERN = re.compile(r"</?[A-Za-z][^>]*>")
_HYPHEN_BREAK = re.compile(r"([A-Za-z])-\n\s*([a-z])")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:)\]])")
_SPACE_AFTER_OPEN = re.compile(r"([(\[])\s+")
_WHITESPACE = re.compile(r"\s+")


def format_subscript(inner: str) -> str:
    """Collapse a subscript and lowercase a single letter so NO + X becomes NOx."""
    cleaned = re.sub(r"\s+", "", TAG_PATTERN.sub("", inner))
    if re.fullmatch(r"[A-Za-z]", cleaned):
        return cleaned.lower()
    return cleaned


def normalize_markup(value: str) -> str:
    """Normalize a string that may still contain Federal Register or HTML tags."""
    normalized = value
    for pattern in SUBSCRIPT_PATTERNS:
        normalized = pattern.sub(_replace_subscript, normalized)
    normalized = TAG_PATTERN.sub("", normalized)
    return normalize_text(normalized)


def _replace_subscript(match: re.Match[str]) -> str:
    inner = match.group(match.lastindex or 1)
    return format_subscript(inner)


def normalize_text(value: str) -> str:
    """Apply the plain-text cleanup shared by parsed XML and raw text."""
    cleaned = unicodedata.normalize("NFKC", value)
    cleaned = cleaned.replace("\u00ad", "")
    cleaned = cleaned.replace("\u2009", " ").replace("\u00a0", " ").replace("\u2007", " ")
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _HYPHEN_BREAK.sub(r"\1\2", cleaned)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = _SPACE_AFTER_OPEN.sub(r"\1", cleaned)
    return cleaned.strip()


def has_residual_markup(value: str) -> bool:
    return RESIDUAL_TAG_PATTERN.search(value) is not None
