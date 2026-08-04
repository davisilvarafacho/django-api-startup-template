import functools
import inspect
import warnings
from collections.abc import Callable, Sequence
from typing import ParamSpec, TypeVar

from internal_frameworks.guardrails.base import GuardrailCheck
from internal_frameworks.guardrails.violations import GuardrailViolation, GuardrailViolationError, GuardrailViolationWarning

P = ParamSpec("P")
T = TypeVar("T")


def guardrail(*, checks: Sequence[GuardrailCheck], raise_exception: bool = False) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Observe a synchronous function with the configured operational checks."""
    configured_checks = tuple(checks)
    if not configured_checks:
        raise ValueError("guardrail requires at least one check")
    if not all(isinstance(check, GuardrailCheck) for check in configured_checks):
        raise TypeError("all checks must be instances of GuardrailCheck")
    if type(raise_exception) is not bool:
        raise TypeError("raise_exception must be a bool")

    def decorator(function: Callable[P, T]) -> Callable[P, T]:
        if inspect.iscoroutinefunction(function):
            raise TypeError("guardrail does not support coroutine functions")
        if inspect.isgeneratorfunction(function):
            raise TypeError("guardrail does not support generator functions")

        @functools.wraps(function)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
            started: list[tuple[int, GuardrailCheck, object]] = []
            violations: dict[int, GuardrailViolation] = {}
            primary_error: BaseException | None = None
            result: T | None = None

            try:
                for index, check in enumerate(configured_checks):
                    started.append((index, check, check.start()))
                result = function(*args, **kwargs)
            except BaseException as error:
                primary_error = error

            finish_errors: list[BaseException] = []
            for index, check, state in reversed(started):
                try:
                    violation = check.finish(state)
                    if violation is not None:
                        violations[index] = violation
                except BaseException as error:
                    finish_errors.append(error)

            if primary_error is not None:
                for error in finish_errors:
                    primary_error.add_note(f"Guardrail cleanup failed: {type(error).__name__}: {error}")
                raise primary_error

            if finish_errors:
                first_error, *remaining_errors = finish_errors
                for error in remaining_errors:
                    first_error.add_note(f"Guardrail cleanup failed: {type(error).__name__}: {error}")
                raise first_error

            ordered_violations = tuple(violations[index] for index in range(len(configured_checks)) if index in violations)
            if ordered_violations:
                if raise_exception:
                    raise GuardrailViolationError(ordered_violations)
                for violation in ordered_violations:
                    warnings.warn(GuardrailViolationWarning(violation), stacklevel=2)

            return result  # type: ignore[return-value]

        return wrapper

    return decorator
