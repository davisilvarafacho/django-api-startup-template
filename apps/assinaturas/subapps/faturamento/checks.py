from django.conf import settings
from django.core.checks import Error, register


@register()
def configuracao_faturamento_check(app_configs, **kwargs):
    del app_configs, kwargs
    stripe = settings.CHECKOUT_VARIANTS.get("stripe")
    if not stripe or not all(stripe[1].get(key) for key in ("api_key", "webhook_secret")):
        return [Error("Credenciais Stripe não configuradas.", id="faturamento.E001")]
    return []
