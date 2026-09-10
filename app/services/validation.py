from app.domain.schemas import ModelAnalysis
from app.services.retrieval import RetrievedPolicy


def validate_analysis(
    analysis: ModelAnalysis,
    retrieved_policies: list[RetrievedPolicy],
    confidence_threshold: float,
) -> list[str]:
    errors: list[str] = []
    policy_index = {(policy.id, policy.version): policy for policy in retrieved_policies}

    if analysis.confidence < confidence_threshold:
        errors.append("confidence_below_review_threshold")

    for citation in analysis.citations:
        policy = policy_index.get((citation.policy_id, citation.policy_version))
        if policy is None:
            errors.append(f"citation_not_retrieved:{citation.policy_id}:{citation.policy_version}")
        elif citation.quote not in policy.content:
            errors.append(f"citation_quote_not_found:{citation.policy_id}:{citation.policy_version}")

    return errors
