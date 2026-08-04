from django.db import connections
from django.test.utils import CaptureQueriesContext

from internal_frameworks.guardrails.base import GuardrailCheck
from internal_frameworks.guardrails.violations import GuardrailViolation


class TooManySqlQueriesCheck(GuardrailCheck):
    """Detects calls that exceed a SQL query budget for one database alias."""

    def __init__(self, max_queries: int = 10, using: str = "default") -> None:
        if isinstance(max_queries, bool) or not isinstance(max_queries, int) or max_queries < 0:
            raise ValueError("max_queries must be an integer greater than or equal to zero")
        if using not in connections:
            raise ValueError(f"unknown database alias: {using}")

        self.max_queries = max_queries
        self.using = using

    def start(self) -> CaptureQueriesContext:
        context = CaptureQueriesContext(connections[self.using])
        context.__enter__()
        return context

    def finish(self, state: object) -> GuardrailViolation | None:
        context = state
        if not isinstance(context, CaptureQueriesContext):
            raise TypeError("state must be a CaptureQueriesContext")

        try:
            observed_queries = len(context)
        finally:
            context.__exit__(None, None, None)

        if observed_queries <= self.max_queries:
            return None

        return GuardrailViolation(
            check=type(self).__name__,
            message=f"SQL query limit exceeded for database '{self.using}'",
            context={"database": self.using, "max_queries": self.max_queries, "observed_queries": observed_queries},
        )
