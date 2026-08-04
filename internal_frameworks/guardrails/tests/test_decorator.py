import warnings

import pytest

from internal_frameworks.guardrails import (
    GuardrailCheck,
    GuardrailViolation,
    GuardrailViolationError,
    GuardrailViolationWarning,
    guardrail,
)


class RecordingCheck(GuardrailCheck):
    def __init__(self, name, events, violation=None, start_error=None, finish_error=None):
        self.name = name
        self.events = events
        self.violation = violation
        self.start_error = start_error
        self.finish_error = finish_error

    def start(self):
        self.events.append(f"start:{self.name}")
        if self.start_error:
            raise self.start_error
        return object()

    def finish(self, state):
        self.events.append(f"finish:{self.name}")
        if self.finish_error:
            raise self.finish_error
        return self.violation


def violation(name):
    return GuardrailViolation(check=name, message=f"{name} failed", context={"count": 1})


def test_violation_copies_context_into_an_immutable_mapping():
    context = {"count": 1}

    item = GuardrailViolation(check="check", message="failed", context=context)
    context["count"] = 2

    assert item.context == {"count": 1}
    with pytest.raises(TypeError):
        item.context["count"] = 3


def test_runs_checks_around_function_and_preserves_metadata_and_return_value():
    events = []

    @guardrail(checks=[RecordingCheck("first", events), RecordingCheck("second", events)])
    def add(left, right):
        """Adds numbers."""
        events.append("function")
        return left + right

    assert add(1, 2) == 3
    assert add.__name__ == "add"
    assert add.__doc__ == "Adds numbers."
    assert events == ["start:first", "start:second", "function", "finish:second", "finish:first"]


@pytest.mark.parametrize(
    ("checks", "raise_exception", "error"),
    [
        ([], False, "at least one"),
        ([object()], False, "GuardrailCheck"),
        ([RecordingCheck("one", [])], 1, "bool"),
    ],
)
def test_rejects_invalid_decorator_configuration(checks, raise_exception, error):
    with pytest.raises((TypeError, ValueError), match=error):
        guardrail(checks=checks, raise_exception=raise_exception)


def test_rejects_async_and_generator_functions():
    async def asynchronous():
        return None

    def generator():
        yield None

    with pytest.raises(TypeError, match="coroutine"):
        guardrail(checks=[RecordingCheck("one", [])])(asynchronous)

    with pytest.raises(TypeError, match="generator"):
        guardrail(checks=[RecordingCheck("one", [])])(generator)


def test_emits_one_stable_warning_per_violation_and_returns_normally():
    events = []

    @guardrail(checks=[RecordingCheck("first", events, violation("first")), RecordingCheck("second", events, violation("second"))])
    def guarded():
        return "result"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert guarded() == "result"

    assert [item.category for item in caught] == [GuardrailViolationWarning, GuardrailViolationWarning]
    assert [item.message.violation.check for item in caught] == ["first", "second"]


def test_raises_violations_in_declaration_order():
    events = []

    @guardrail(
        checks=[RecordingCheck("first", events, violation("first")), RecordingCheck("second", events, violation("second"))],
        raise_exception=True,
    )
    def guarded():
        return "result"

    with pytest.raises(GuardrailViolationError) as captured:
        guarded()

    assert [item.check for item in captured.value.violations] == ["first", "second"]


def test_preserves_function_error_after_finalizing_checks():
    events = []

    @guardrail(checks=[RecordingCheck("one", events, violation("one"))], raise_exception=True)
    def guarded():
        raise LookupError("original")

    with pytest.raises(LookupError, match="original"):
        guarded()

    assert events == ["start:one", "finish:one"]


def test_finalizes_started_checks_when_later_start_fails_and_adds_finish_notes():
    events = []
    start_error = RuntimeError("start failed")
    finish_error = OSError("finish failed")

    @guardrail(checks=[RecordingCheck("first", events, finish_error=finish_error), RecordingCheck("second", events, start_error=start_error)])
    def guarded():
        pytest.fail("the function must not execute")

    with pytest.raises(RuntimeError, match="start failed") as captured:
        guarded()

    assert events == ["start:first", "start:second", "finish:first"]
    assert captured.value.__notes__ == ["Guardrail cleanup failed: OSError: finish failed"]


def test_raises_first_finish_error_after_attempting_all_cleanups():
    events = []

    @guardrail(
        checks=[RecordingCheck("first", events, finish_error=OSError("first")), RecordingCheck("second", events, finish_error=RuntimeError("second"))]
    )
    def guarded():
        return None

    with pytest.raises(RuntimeError, match="second") as captured:
        guarded()

    assert events == ["start:first", "start:second", "finish:second", "finish:first"]
    assert captured.value.__notes__ == ["Guardrail cleanup failed: OSError: first"]


def test_keeps_state_isolated_between_calls():
    states = []

    class StatefulCheck(GuardrailCheck):
        def start(self):
            state = []
            states.append(state)
            return state

        def finish(self, state):
            state.append("finished")
            return

    @guardrail(checks=[StatefulCheck()])
    def guarded():
        return None

    guarded()
    guarded()

    assert states == [["finished"], ["finished"]]
