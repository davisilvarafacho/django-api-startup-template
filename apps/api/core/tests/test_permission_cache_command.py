from io import StringIO
from unittest.mock import patch

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
