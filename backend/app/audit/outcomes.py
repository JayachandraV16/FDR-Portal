import enum


class AuditOutcome(enum.StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"
