from abc import ABC, abstractmethod

from internal_frameworks.guardrails.violations import GuardrailViolation


class GuardrailCheck(ABC):
    """Contract for a synchronous execution guardrail."""

    @abstractmethod
    def start(self) -> object:
        """Start observing and return state exclusive to this invocation."""

    @abstractmethod
    def finish(self, state: object) -> GuardrailViolation | None:
        """Finish observing and return a violation when the limit was exceeded."""
