"""Estado persistido do ciclo de vida da conta."""

from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest

from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


def test_estado_de_verificacao_e_exclusao_tem_datas_opcionais():
    usuario = criar_usuario()

    for nome in ("email_verificado_em", "exclusao_solicitada_em", "exclusao_agendada_para"):
        campo = usuario._meta.get_field(nome)
        assert campo.null is True
        assert campo.blank is True


def test_alterar_email_diretamente_limpa_a_verificacao():
    usuario = criar_usuario()
    usuario.email_verificado_em = timezone.now()
    usuario.save(update_fields=["email_verificado_em"])

    usuario.email = "novo-email@exemplo.com"
    usuario.save(update_fields=["email"])

    usuario.refresh_from_db()
    assert usuario.email_verificado_em is None


def test_atualizar_email_por_queryset_limpa_a_verificacao():
    usuario = criar_usuario()
    usuario.email_verificado_em = timezone.now()
    usuario.save(update_fields=["email_verificado_em"])

    usuario.__class__.objects.filter(pk=usuario.pk).update(email="novo-email-por-queryset@exemplo.com")

    usuario.refresh_from_db()
    assert usuario.email == "novo-email-por-queryset@exemplo.com"
    assert usuario.email_verificado_em is None


def test_atualizar_email_por_manager_base_limpa_a_verificacao_e_enxerga_soft_delete():
    usuario = criar_usuario()
    usuario.email_verificado_em = timezone.now()
    usuario.save(update_fields=["email_verificado_em"])

    usuario.__class__._base_manager.filter(pk=usuario.pk).update(email="novo-email-por-manager-base@exemplo.com")

    usuario.delete()
    excluido = usuario.__class__._base_manager.get(pk=usuario.pk)
    assert excluido.email_verificado_em is None
    assert excluido.is_deleted is True


def test_salvar_outro_campo_preserva_a_verificacao_do_email():
    usuario = criar_usuario()
    verificado_em = timezone.now()
    usuario.email_verificado_em = verificado_em
    usuario.save(update_fields=["email_verificado_em"])

    usuario.first_name = "Outro nome"
    usuario.save(update_fields=["first_name"])

    usuario.refresh_from_db()
    assert usuario.email_verificado_em == verificado_em


def test_email_e_unico_sem_distinguir_maiusculas_de_minusculas_e_soft_delete_libera_o_endereco():
    original = criar_usuario(email="Compartilhado@example.com")

    with transaction.atomic():
        with pytest.raises(IntegrityError):
            criar_usuario(email="compartilhado@example.com")

    original.delete()
    recriado = criar_usuario(email="compartilhado@example.com")
    assert recriado.email == "compartilhado@example.com"
