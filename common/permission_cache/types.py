import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = 1


class InvalidEnvelope(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DjangoPermissionSnapshot:
    user_permissions: frozenset[str]
    group_permissions: frozenset[str]

    @property
    def all_permissions(self) -> frozenset[str]:
        return self.user_permissions | self.group_permissions


@dataclass(frozen=True, slots=True)
class GuardianPermissionSnapshot:
    user_permissions: frozenset[str]
    group_permissions: frozenset[str]

    @property
    def all_permissions(self) -> frozenset[str]:
        return self.user_permissions | self.group_permissions


@dataclass(frozen=True, slots=True)
class TenantAccess:
    organization_id: int
    organization_slug: str
    membership_id: int
    role: int

    def has_minimum_role(self, minimum_role: int) -> bool:
        return self.role >= minimum_role


def encode_envelope(payload: dict[str, Any] | None) -> str:
    envelope = {"schema": SCHEMA_VERSION, "found": payload is not None}
    if payload is not None:
        envelope["payload"] = payload
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True)


def decode_envelope(raw: object) -> dict[str, Any]:
    try:
        envelope = json.loads(raw) if isinstance(raw, str) else None
    except (TypeError, ValueError) as exc:
        raise InvalidEnvelope("Payload do cache de autorização não é JSON válido.") from exc
    if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA_VERSION:
        raise InvalidEnvelope("Schema desconhecido no cache de autorização.")
    if not isinstance(envelope.get("found"), bool):
        raise InvalidEnvelope("Envelope sem marcador found válido.")
    if envelope["found"] and not isinstance(envelope.get("payload"), dict):
        raise InvalidEnvelope("Envelope positivo sem payload primitivo.")
    return envelope
