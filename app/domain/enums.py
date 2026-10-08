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


class QuestionStatus(StrEnum):
    QUEUED = "queued"
    ANSWERING = "answering"
    ANSWERED = "answered"
    UNANSWERED = "unanswered"
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


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
