"""Abbreviation pairs used to connect keyword overlap with regulatory shorthand.

Expansion is phrase-scoped. A document that says "oxides" does not gain an NOx token,
and a document that contains none of these forms keeps the original token set so the
lexical baseline score does not move.
"""

import re

SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("nox", "nitrogen oxides"),
    ("voc", "volatile organic compounds"),
    ("pm2.5", "fine particulate matter"),
    ("hap", "hazardous air pollutants"),
    ("ghg", "greenhouse gas"),
    ("naaqs", "national ambient air quality standards"),
    ("neshap", "national emission standards for hazardous air pollutants"),
    ("nsps", "new source performance standards"),
    ("sip", "state implementation plan"),
)

_COMPACT_FORM = re.compile(r"[a-z0-9.]{3,}")


def expand_tokens(text: str, tokens: set[str]) -> set[str]:
    """Add every form in a group once any one form is present in the text."""
    expanded = set(tokens)
    folded = re.sub(r"\s+", " ", text.lower())
    for group in SYNONYM_GROUPS:
        if not any(_form_present(folded, form) for form in group):
            continue
        for form in group:
            expanded.update(re.findall(r"[a-z0-9]{3,}", form))
            compact = form.replace(" ", "")
            if _COMPACT_FORM.fullmatch(compact):
                expanded.add(compact)
    return expanded


def _form_present(folded: str, form: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(form.lower())}(?![a-z0-9])"
    return re.search(pattern, folded) is not None
