from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class GuardrailViolation:
    check: str
    message: str
    context: Mapping[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


class GuardrailViolationError(RuntimeError):
    """Raised when one or more guardrails are configured to fail execution."""

    def __init__(self, violations: tuple[GuardrailViolation, ...]) -> None:
        self.violations = violations
        super().__init__("; ".join(violation.message for violation in violations))


class GuardrailViolationWarning(Warning):
    """Warning emitted for one guardrail violation."""

    def __init__(self, violation: GuardrailViolation) -> None:
        self.violation = violation
        super().__init__(violation.message)
