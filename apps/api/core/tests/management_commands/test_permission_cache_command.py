from io import StringIO
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest


def test_command_prints_only_new_global_epoch():
    stdout = StringIO()
    with patch(
        "apps.api.core.management.commands.invalidate_permission_cache.bump_epoch_scopes",
        return_value={"global": 9001},
    ) as bump:
        call_command("invalidate_permission_cache", stdout=stdout)

    assert stdout.getvalue().strip() == "9001"
    bump.assert_called_once_with(("global",), database_alias="default", layer="django", raise_errors=True)


def test_command_returns_nonzero_on_redis_failure():
    with patch(
        "apps.api.core.management.commands.invalidate_permission_cache.bump_epoch_scopes",
        side_effect=ConnectionError("redis down"),
    ):
        with pytest.raises(CommandError, match="Não foi possível invalidar o cache de permissões."):
            call_command("invalidate_permission_cache")


def test_command_does_not_log_or_print_redis_connection_details(caplog, capsys):
    stderr = StringIO()
    sentinels = ("sentinel-host", "sentinel-key", "sentinel-token")
    store = Mock()
    store.bump.side_effect = ConnectionError("redis://sentinel-user:sentinel-token@sentinel-host:6379/4 key=sentinel-key")

    with patch("internal_frameworks.permission_cache.invalidation.EpochStore", return_value=store):
        with pytest.raises(CommandError) as error:
            call_command("invalidate_permission_cache", stderr=stderr)

    captured = capsys.readouterr()
    assert str(error.value) == "Não foi possível invalidar o cache de permissões."
    for sentinel in sentinels:
        assert sentinel not in caplog.text
        assert sentinel not in captured.err
        assert sentinel not in stderr.getvalue()
