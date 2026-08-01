"""Auditoria de eventos de ciclo de vida de API keys.

Centraliza a emissão: services chamam isso depois da própria transação,
nunca as views (evita duplicar o mesmo evento em dois lugares). Nunca inclui
plain token, digest ou token_key — só metadados estáveis (UUID, responsável,
criador, organização e request ID).
"""

import logging

import posthog
from posthog import capture, identify_context, new_context

from apps.api.core.request_id import get_request_id

logger = logging.getLogger("api.audit")


def emit_api_key_event(event, *, instance, actor, **extra):
    """Registra um evento auditável de API key em log estruturado e PostHog.

    `event` é um dos: create, rotate, suspend, resume, revoke,
    responsible_changed, scopes_changed.
    """
    payload = {
        "event": event,
        "token_uuid": str(instance.uuid),
        "responsavel_id": instance.responsavel_id,
        "created_by_id": instance.created_by_id,
        "organization_id": instance.organization_id,
        "actor_id": getattr(actor, "pk", None),
        "request_id": get_request_id(),
        **extra,
    }

    logger.info("api_key_%s", event, extra=payload)

    with new_context():
        if actor is not None:
            identify_context(str(actor.pk))
        posthog.tag("token_uuid", payload["token_uuid"])
        if payload["organization_id"] is not None:
            posthog.tag("organization_id", payload["organization_id"])
        capture(f"api_key_{event}", properties=payload)
