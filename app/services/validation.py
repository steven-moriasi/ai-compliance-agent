import re

from app.domain.schemas import ModelAnalysis
from app.services.retrieval import RetrievedPolicy

MINIMUM_QUOTE_LENGTH = 20
WHITESPACE_PATTERN = re.compile(r"\s+")
MONTH_PATTERN = (
    r"January|February|March|April|May|June|July|August|September|October|November|December"
)
DATE_VALUE_PATTERN = (
    rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}"
    rf"|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}"
    rf"|\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+\d{{4}}"
    rf"|(?:{MONTH_PATTERN})\s+\d{{1,2}}(?:,\s*|\s+)\d{{4}})"
)
DURATION_VALUE_PATTERN = (
    r"\d+(?:\.\d+)?\s+(?:(?:business|calendar|working)\s+)?"
    r"(?:minute|hour|day|week|month|year)s?"
)
TEMPORAL_PATTERNS = (
    re.compile(
        rf"\b(?:within|after|before|for|by|on|until|no later than)\s+"
        rf"(?:{DATE_VALUE_PATTERN}|{DURATION_VALUE_PATTERN})\b",
        re.IGNORECASE,
    ),
    re.compile(rf"\b{DATE_VALUE_PATTERN}\b", re.IGNORECASE),
    re.compile(rf"\b{DURATION_VALUE_PATTERN}\b", re.IGNORECASE),
)


def validate_analysis(
    analysis: ModelAnalysis,
    retrieved_policies: list[RetrievedPolicy],
    confidence_threshold: float,
) -> list[str]:
    errors: list[str] = []
    policy_index = {
        (policy.id, policy.version, policy.section_ref): policy for policy in retrieved_policies
    }
    cited_passages: list[str] = []

    if analysis.confidence < confidence_threshold:
        errors.append("confidence_below_review_threshold")

    for citation in analysis.citations:
        citation_key = (
            citation.policy_id,
            citation.policy_version,
            citation.section_ref,
        )
        policy = policy_index.get(citation_key)
        source_ref = f"{citation.policy_id}:{citation.policy_version}:{citation.section_ref}"
        if policy is None:
            errors.append(f"citation_not_retrieved:{source_ref}")
            continue
        normalized_quote = _normalize_whitespace(citation.quote)
        if len(normalized_quote) < MINIMUM_QUOTE_LENGTH:
            errors.append(f"citation_quote_too_short:{source_ref}")
            continue
        normalized_content = _normalize_whitespace(policy.content)
        if not _contains_word_bounded(normalized_content, normalized_quote):
            errors.append(f"citation_quote_not_found:{source_ref}")
            continue
        cited_passages.append(normalized_quote)

    for claim in _extract_temporal_claims(analysis.rationale):
        if not any(_contains_word_bounded(passage, claim) for passage in cited_passages):
            errors.append(f"rationale_temporal_claim_not_cited:{claim}")

    return errors


def _normalize_whitespace(value: str) -> str:
    return WHITESPACE_PATTERN.sub(" ", value).strip()


def _contains_word_bounded(value: str, candidate: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", value) is not None


def _extract_temporal_claims(value: str) -> list[str]:
    normalized = _normalize_whitespace(value)
    matches: list[tuple[int, int, str]] = []
    for pattern in TEMPORAL_PATTERNS:
        for match in pattern.finditer(normalized):
            start, end = match.span()
            if any(
                start < existing_end and end > existing_start
                for existing_start, existing_end, _ in matches
            ):
                continue
            matches.append((start, end, match.group()))
    return [match for _, _, match in sorted(matches)]
