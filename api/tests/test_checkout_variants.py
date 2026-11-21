"""Variantes de checkout por ambiente (não tocam o banco)."""

from api.configure_enviroment import build_checkout_variants


def test_sem_chave_fora_de_producao_nao_registra_stripe():
    variantes = build_checkout_variants(api_key="", webhook_secret="", in_production=False)

    assert "stripe" not in variantes
    assert variantes == {}


def test_com_chave_registra_stripe_com_kwargs_da_lib():
    variantes = build_checkout_variants(api_key="sk_test_x", webhook_secret="whsec_x", in_production=False)

    assert variantes == {
        "stripe": (
            "django_checkouts.gateways.stripe.StripeGateway",
            {"api_key": "sk_test_x", "webhook_secret": "whsec_x"},
        ),
    }


def test_sem_chave_em_producao_registra_stripe_para_o_check_falhar_alto():
    variantes = build_checkout_variants(api_key="", webhook_secret="", in_production=True)

    assert "stripe" in variantes
