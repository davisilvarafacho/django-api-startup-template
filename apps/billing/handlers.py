"""Handlers do app de cobrança."""

from django.conf import settings

from apps.billing.models import Plan


def ensure_free_plan(**kwargs):
    """Garante o plano gratuito depois das migrations (handler de `post_migrate`).

    Idempotente. O `post_migrate` dispara para todos os apps, então só age quando o
    app emissor é `billing`. Usa `all_objects` porque a constraint de unicidade do
    gratuito também vale para linhas excluídas: se ele estiver com soft delete, é
    restaurado em vez de recriado.

    Args:
        **kwargs: Argumentos enviados pelo sinal `post_migrate` (`sender`, `using`).
    """
    if kwargs["sender"].label != "billing":
        return

    using = kwargs["using"]
    gratuito = Plan.all_objects.using(using).filter(is_free=True).first()
    if gratuito is None:
        Plan.objects.using(using).create(
            name="Gratuito",
            slug="gratuito",
            rank=0,
            member_limit=settings.BILLING_FREE_PLAN_MEMBER_LIMIT,
            is_free=True,
        )
        return

    if gratuito.is_deleted:
        gratuito.is_deleted = False
        gratuito.save(using=using, update_fields=["is_deleted"])
