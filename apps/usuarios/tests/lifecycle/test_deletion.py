"""Exclusão de conta em duas fases."""

import importlib
import json
from datetime import timedelta

from django.conf import settings
from django.test import override_settings
from django.utils import timezone

import pytest
from auditlog.context import set_actor
from celery.schedules import crontab

from apps.api.autenticacao.mfa import create_trusted_device
from apps.api.autenticacao.models import (
    AuthToken,
    IdentidadeExterna,
    MFAFactor,
    MFAFactorType,
    MFARecoveryCode,
    ProvedorIdentidade,
    TokenType,
)
from apps.api.core.errors import APIError
from apps.logs.models import LogAlteracao
from apps.logs.serializers import LogAlteracaoSerpySerializer
from apps.organizacoes.models import Convite, Organizacao, Papel, Vinculo
from apps.usuarios import accounts
from apps.usuarios.accounts import Contas
from apps.usuarios.models import Usuario
from tests.support.usuarios import criar_usuario

pytestmark = pytest.mark.django_db


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7)
def test_agendar_exclusao_preserva_a_conta_durante_sete_dias_e_a_desativa():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Organização", slug="agendamento")
    vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO)

    Contas.agendar_exclusao(usuario)

    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    assert usuario.exclusao_solicitada_em is not None
    assert usuario.exclusao_agendada_para - usuario.exclusao_solicitada_em == timedelta(days=7)
    assert usuario.is_active is False
    assert usuario.is_deleted is False
    assert vinculo.is_active is False
    assert vinculo.is_deleted is False


@override_settings(ACCOUNT_DELETION_GRACE_DAYS=7)
def test_novo_agendamento_retorna_conflito_sem_reiniciar_o_prazo():
    usuario = criar_usuario()
    primeiro = Contas.agendar_exclusao(usuario)
    data_original = primeiro.exclusao_agendada_para

    with pytest.raises(APIError) as excinfo:
        Contas.agendar_exclusao(usuario)

    usuario.refresh_from_db()
    assert excinfo.value.code == "account.deletion_already_scheduled"
    assert excinfo.value.status_code == 409
    assert excinfo.value.context == {"scheduled_for": data_original.isoformat()}
    assert usuario.exclusao_agendada_para == data_original


def test_unico_proprietario_ativo_nao_pode_agendar_exclusao():
    usuario = criar_usuario()
    organizacao = Organizacao.objects.create(nome="Organização", slug="proprietario-exclusao")
    Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.PROPRIETARIO)

    with pytest.raises(APIError) as excinfo:
        Contas.agendar_exclusao(usuario)

    usuario.refresh_from_db()
    assert excinfo.value.code == "account.owner_transfer_required"
    assert usuario.is_active is True
    assert usuario.exclusao_agendada_para is None


def test_anonimizacao_vencida_remove_pii_acessos_identidade_vinculos_e_convites():
    email_original = "apagar@example.com"
    sub_original = "google-sub-secreto"
    nome_historico = "PrimeiroNomeHistorico"
    nome_atual = "PrimeiroNomeAtual"
    sobrenome = "SobrenomeConfidencial"
    telefone = "+5511999999999"
    email_historico_do_ator = "email-historico-do-ator@example.com"
    usuario = criar_usuario(email=email_original, first_name=nome_historico, last_name=sobrenome)
    usuario.first_name = nome_atual
    usuario.phone_number = telefone
    usuario.phone_verified_at = timezone.now()
    usuario.email_verificado_em = timezone.now()
    with set_actor(usuario):
        usuario.save(update_fields=["first_name", "phone_number", "phone_verified_at", "email_verificado_em"])
        organizacao = Organizacao.objects.create(nome="Organização Financeira", slug="anonimizacao")
        vinculo = Vinculo.objects.create(usuario=usuario, organizacao=organizacao, papel=Papel.MEMBRO, is_active=False)
        convite = Convite.objects.create(organizacao=organizacao, email=email_original, convidado_por=usuario)
        sub_excluido_original = "google-sub-excluido-secreto"
        identidade_excluida = IdentidadeExterna.objects.create(
            usuario=usuario,
            provedor=ProvedorIdentidade.GOOGLE,
            identificador=sub_excluido_original,
        )
        identidade_excluida.delete()
        identidade = IdentidadeExterna.objects.create(
            usuario=usuario,
            provedor=ProvedorIdentidade.GOOGLE,
            identificador=sub_original,
        )

    # Representa uma linha legada, criada pelo auditlog antes das exclusões de
    # campos sensíveis atuais, para provar a limpeza de todas as colunas livres.
    log_legado = LogAlteracao.objects.get_for_object(identidade).latest("timestamp")
    log_legado.actor = usuario
    log_legado.actor_email = email_original
    log_legado.object_repr = f"Identidade {sub_original} de {nome_historico}"
    log_legado.changes = {
        "identificador": [None, sub_original],
        "phone_number": [None, telefone],
        "first_name": [nome_historico, nome_atual],
        "technical_status": ["pending", "linked"],
    }
    log_legado.changes_text = f"{email_original}; {sub_original}; {telefone}"
    log_legado.serialized_data = {
        "model": "autenticacao.identidadeexterna",
        "pk": identidade.pk,
        "fields": {"identificador": sub_original, "provedor": ProvedorIdentidade.GOOGLE},
    }
    log_legado.additional_data = {"email": email_original, "nome": nome_historico, "technical_id": usuario.pk}
    log_legado.save(update_fields=["actor", "actor_email", "object_repr", "changes", "changes_text", "serialized_data", "additional_data"])

    log_financeiro = LogAlteracao.objects.get_for_object(organizacao).latest("timestamp")
    log_financeiro.actor_email = email_historico_do_ator
    log_financeiro.additional_data = {"actor_email": email_historico_do_ator, "invoice_id": 8675309}
    log_financeiro.save(update_fields=["actor_email", "additional_data"])
    historico_financeiro = {
        "id": log_financeiro.pk,
        "object_repr": log_financeiro.object_repr,
        "changes": log_financeiro.changes,
        "action": log_financeiro.action,
        "timestamp": log_financeiro.timestamp,
    }
    token, _ = AuthToken.objects.create(responsavel=usuario, type=TokenType.TOKEN)
    dispositivo = create_trusted_device(usuario, {"device_name": "Notebook"}).instance
    fator = MFAFactor.objects.create(
        user=usuario,
        type=MFAFactorType.TOTP,
        secret="ABCDEFGHIJKLMNOPQRSTUVWX23456789",
        confirmed_at=timezone.now(),
        enabled_at=timezone.now(),
    )
    recuperacao = MFARecoveryCode.objects.create(user=usuario, digest="digest")
    agora = timezone.now()
    Usuario.all_objects.filter(pk=usuario.pk).update(
        is_active=False,
        exclusao_solicitada_em=agora - timedelta(days=7),
        exclusao_agendada_para=agora - timedelta(seconds=1),
    )
    audit_ids_antes = set(LogAlteracao.objects.values_list("pk", flat=True))

    processadas = accounts.anonimizar_contas_vencidas(now=agora, batch_size=10)

    usuario = Usuario.all_objects.get(pk=usuario.pk)
    identidade = IdentidadeExterna.all_objects.get(pk=identidade.pk)
    identidade_excluida = IdentidadeExterna.all_objects.get(pk=identidade_excluida.pk)
    vinculo = Vinculo.all_objects.get(pk=vinculo.pk)
    convite = Convite.all_objects.get(pk=convite.pk)
    token.refresh_from_db()
    dispositivo.refresh_from_db()
    fator.refresh_from_db()
    recuperacao.refresh_from_db()
    assert processadas == 1
    assert usuario.email.startswith(f"deleted-{usuario.pk}-")
    assert usuario.email.endswith("@invalid.local")
    assert usuario.first_name == ""
    assert usuario.last_name == ""
    assert usuario.phone_number is None
    assert usuario.phone_verified_at is None
    assert usuario.email_verificado_em is None
    assert usuario.has_usable_password() is False
    assert usuario.is_active is False
    assert usuario.is_deleted is True
    assert identidade.identificador != sub_original
    assert identidade.is_active is False
    assert identidade.is_deleted is True
    assert identidade_excluida.identificador != sub_excluido_original
    assert token.revoked_at is not None
    assert dispositivo.revoked_at is not None
    assert fator.disabled_at is not None
    assert recuperacao.consumed_at is not None
    assert vinculo.is_deleted is True
    assert convite.is_deleted is True
    assert accounts.anonimizar_contas_vencidas(now=agora, batch_size=10) == 0
    assert criar_usuario(email=email_original).email == email_original
    audit_ids_depois = set(LogAlteracao.objects.values_list("pk", flat=True))
    assert audit_ids_antes <= audit_ids_depois
    log_legado.refresh_from_db()
    assert log_legado.changes == {"technical_status": ["pending", "linked"]}
    assert log_legado.serialized_data["pk"] == identidade.pk
    assert log_legado.serialized_data["fields"] == {"provedor": ProvedorIdentidade.GOOGLE}
    assert log_legado.additional_data == {"technical_id": usuario.pk}
    log_financeiro.refresh_from_db()
    assert {
        "id": log_financeiro.pk,
        "object_repr": log_financeiro.object_repr,
        "changes": log_financeiro.changes,
        "action": log_financeiro.action,
        "timestamp": log_financeiro.timestamp,
    } == historico_financeiro
    assert log_financeiro.actor_email is None
    assert log_financeiro.additional_data == {"actor_email": "[redacted]", "invoice_id": 8675309}
    auditoria = json.dumps(
        {
            "rows": list(
                LogAlteracao.objects.values(
                    "id",
                    "content_type_id",
                    "object_pk",
                    "object_id",
                    "object_repr",
                    "serialized_data",
                    "action",
                    "changes_text",
                    "changes",
                    "actor_id",
                    "timestamp",
                    "additional_data",
                    "actor_email",
                )
            ),
            "api": LogAlteracaoSerpySerializer(LogAlteracao.objects.select_related("actor", "content_type"), many=True).data,
        },
        default=str,
    )
    for pii in (
        email_original,
        usuario.email,
        sub_original,
        sub_excluido_original,
        nome_historico,
        nome_atual,
        sobrenome,
        telefone,
        email_historico_do_ator,
    ):
        assert pii not in auditoria


@override_settings(ACCOUNT_DELETION_BATCH_SIZE=1)
def test_task_anonimiza_no_maximo_o_lote_configurado_por_execucao(caplog):
    agora = timezone.now()
    contas = [criar_usuario(email=f"lote-{indice}@example.com") for indice in range(2)]
    Usuario.all_objects.filter(pk__in=[conta.pk for conta in contas]).update(
        is_active=False,
        exclusao_solicitada_em=agora - timedelta(days=7),
        exclusao_agendada_para=agora - timedelta(seconds=1),
    )

    caplog.set_level("INFO", logger="apps.usuarios.tasks")
    tasks = importlib.import_module("apps.usuarios.tasks")
    tasks.anonimizar_contas_vencidas()

    assert Usuario.all_objects.filter(pk__in=[conta.pk for conta in contas], is_deleted=True).count() == 1
    assert all(conta.email not in caplog.text for conta in contas)
    tasks.anonimizar_contas_vencidas()
    assert Usuario.all_objects.filter(pk__in=[conta.pk for conta in contas], is_deleted=True).count() == 2


def test_task_periodica_usa_lote_configuravel():
    tasks = importlib.import_module("apps.usuarios.tasks")
    entry = settings.CELERY_BEAT_SCHEDULE["anonymize-expired-accounts"]

    assert tasks.anonimizar_contas_vencidas.name == "usuarios.anonimizar_contas_vencidas"
    assert tasks.anonimizar_contas_vencidas.ignore_result is True
    assert tasks.anonimizar_contas_vencidas.autoretry_for
    assert entry["task"] == tasks.anonimizar_contas_vencidas.name
    assert isinstance(entry["schedule"], crontab)
    assert settings.ACCOUNT_DELETION_BATCH_SIZE == 100


def test_lote_precisa_ser_positivo():
    with pytest.raises(ValueError, match="positivo"):
        accounts.anonimizar_contas_vencidas(batch_size=0)
