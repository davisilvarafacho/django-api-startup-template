from common.permission_cache.keys import (
    epoch_key,
    global_scope,
    guardian_object_scope,
    snapshot_key,
    user_scope,
)


def test_keys_include_database_layer_identity_and_epochs():
    assert epoch_key("replica", user_scope("django", 42)) == "epoch:replica:django:user:42"
    assert guardian_object_scope(7, "item/42") == "guardian:object:7:item%2F42"

    key_a = snapshot_key("django", "default", ("user", 42), (10, 20, 30))
    key_b = snapshot_key("django", "other", ("user", 42), (10, 20, 30))

    assert key_a.startswith("snapshot:default:django:")
    assert key_a.endswith(":10-20-30")
    assert key_a != key_b
    assert global_scope() == "global"
