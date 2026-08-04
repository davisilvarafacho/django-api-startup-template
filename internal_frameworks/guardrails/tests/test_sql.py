import pytest

from internal_frameworks.guardrails import TooManySqlQueriesCheck
from internal_frameworks.guardrails.checks import sql


class FakeCaptureQueriesContext:
    counts_by_connection = {}

    def __init__(self, connection):
        self.connection = connection
        self.entered = False
        self.exited = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *args):
        self.exited = True

    def __len__(self):
        return self.counts_by_connection[self.connection]


@pytest.fixture
def fake_connections(monkeypatch):
    default = object()
    reporting = object()
    FakeCaptureQueriesContext.counts_by_connection = {default: 0, reporting: 0}
    monkeypatch.setattr(sql, "connections", {"default": default, "reporting": reporting})
    monkeypatch.setattr(sql, "CaptureQueriesContext", FakeCaptureQueriesContext)
    return {"default": default, "reporting": reporting}


def test_allows_the_exact_query_limit(fake_connections):
    check = TooManySqlQueriesCheck(max_queries=2)
    FakeCaptureQueriesContext.counts_by_connection[fake_connections["default"]] = 2

    state = check.start()

    assert check.finish(state) is None
    assert state.entered is True
    assert state.exited is True


def test_reports_only_queries_from_the_selected_database_without_sql_content(fake_connections):
    check = TooManySqlQueriesCheck(max_queries=0, using="reporting")
    FakeCaptureQueriesContext.counts_by_connection[fake_connections["default"]] = 100
    FakeCaptureQueriesContext.counts_by_connection[fake_connections["reporting"]] = 1

    violation = check.finish(check.start())

    assert violation is not None
    assert violation.check == "TooManySqlQueriesCheck"
    assert violation.context == {"database": "reporting", "max_queries": 0, "observed_queries": 1}
    assert "SELECT" not in violation.message
    assert "SELECT" not in str(violation.context)


@pytest.mark.parametrize("max_queries", [-1, 1.5, True, "1"])
def test_rejects_invalid_query_limits(fake_connections, max_queries):
    with pytest.raises(ValueError, match="max_queries"):
        TooManySqlQueriesCheck(max_queries=max_queries)


def test_rejects_unknown_database_alias(fake_connections):
    with pytest.raises(ValueError, match="unknown database alias"):
        TooManySqlQueriesCheck(using="missing")
