import hashlib
import json
from urllib.parse import quote


def global_scope() -> str:
    return "global"


def layer_scope(layer: str) -> str:
    return f"{layer}:global"


def user_scope(layer: str, user_id: object) -> str:
    return f"{layer}:user:{quote(str(user_id), safe='')}"


def guardian_object_scope(content_type_id: object, object_pk: object) -> str:
    return f"guardian:object:{quote(str(content_type_id), safe='')}:{quote(str(object_pk), safe='')}"


def epoch_key(database_alias: str, scope: str) -> str:
    return f"epoch:{quote(database_alias, safe='')}:{scope}"


def snapshot_key(layer: str, database_alias: str, identity: tuple[object, ...], epochs: tuple[int, ...]) -> str:
    serialized = json.dumps([str(part) for part in identity], separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.blake2s(serialized.encode(), digest_size=16).hexdigest()
    versions = "-".join(str(epoch) for epoch in epochs)
    return f"snapshot:{quote(database_alias, safe='')}:{layer}:{digest}:{versions}"
