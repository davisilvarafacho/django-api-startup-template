"""Variantes de checkout por ambiente (não tocam o banco)."""

from api.configure_enviroment import build_checkout_variants


def test_sem_chave_fora_de_producao_nao_registra_stripe():
    variantes = build_checkout_variants(api_key="", webhook_secret="", sandbox=True, in_production=False)

    assert "stripe" not in variantes
    assert variantes == {}


def test_com_chave_registra_stripe_com_kwargs_da_lib():
    variantes = build_checkout_variants(api_key="sk_test_x", webhook_secret="whsec_x", sandbox=True, in_production=False)

    assert variantes == {
        "stripe": (
            "django_checkouts.gateways.stripe.StripeGateway",
            {"api_key": "sk_test_x", "webhook_secret": "whsec_x", "sandbox": True},
        ),
    }


def test_sem_chave_em_producao_registra_stripe_completo_para_o_check_falhar_alto():
    variantes = build_checkout_variants(api_key="", webhook_secret="", sandbox=False, in_production=True)

    assert variantes == {
        "stripe": (
            "django_checkouts.gateways.stripe.StripeGateway",
            {"api_key": "", "webhook_secret": "", "sandbox": False},
        ),
    }


def test_sandbox_recebido_e_repassado_a_variante():
    variantes = build_checkout_variants(api_key="sk_live_x", webhook_secret="whsec_x", sandbox=False, in_production=True)

    _, configuracao = variantes["stripe"]
    assert configuracao["sandbox"] is False
