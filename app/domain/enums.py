from enum import StrEnum


class PolicyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class CaseStatus(StrEnum):
    QUEUED = "queued"
    ANALYZING = "analyzing"
    REVIEW_REQUIRED = "review_required"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class AnalysisOutcome(StrEnum):
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    NEEDS_REVIEW = "needs_review"


class ReviewDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class NotificationStatus(StrEnum):
    PENDING = "pending"
    DELIVERING = "delivering"
    SENT = "sent"
    DEAD = "dead"
