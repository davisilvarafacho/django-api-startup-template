import inspect

from django.db import connection, transaction
from django.utils import timezone

import pytest

from apps.assinaturas.subapps.faturamento.models import AssinaturaGateway, EventoCobranca, StatusEventoCobranca
from apps.assinaturas.subapps.faturamento.tasks import processar_evento_cobranca, recuperar_eventos_cobranca
from apps.assinaturas.tests.test_subscription_models import _criar_assinatura, _criar_versao
from apps.organizacoes.context import organizacao_atual_privilegiada
from apps.organizacoes.models import Organizacao


def _evento_recebido_sem_tenant(*, identificador_assinatura: str) -> int:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute(
            """INSERT INTO evento_cobranca
               (created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,
                identificador_assinatura,identificador_checkout,identificador_fatura,status,exige_tenant,
                tentativas_roteamento,tentativas_processamento,tentativas_automaticas_ciclo,
                payload_normalizado,hash_payload,erro,ocorrido_em)
               VALUES (%s,%s,true,false,'stripe',%s,'subscription.updated',%s,'','',10,true,0,0,0,
                       '{}'::jsonb,%s,'',%s) RETURNING id""",
            [timezone.now(), timezone.now(), f"evt_recovery_{identificador_assinatura}", identificador_assinatura, "e" * 64, timezone.now()],
        )
        return cursor.fetchone()[0]


def test_worker_financeiro_nunca_assume_role_owner_e_exige_tenant_na_mensagem():
    fonte = inspect.getsource(processar_evento_cobranca.run)
    assert "billing_functions_owner" not in fonte
    assert processar_evento_cobranca.run(1, "stripe", None) is False


@pytest.mark.django_db(transaction=True)
def test_recovery_rerouteia_recebido_e_set_local_nao_vaza_role(monkeypatch):
    organizacao = Organizacao.objects.create(nome="Recovery", slug="recovery")
    assinatura = _criar_assinatura(organizacao, _criar_versao(codigo="recovery"))
    AssinaturaGateway.objects.create(
        organizacao=organizacao,
        assinatura=assinatura,
        variante="stripe",
        identificador_externo="sub_recovery",
    )
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_recovery")
    enviados = []
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay",
        lambda *args: enviados.append(args),
    )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('role', true), current_setting('rls.tenant_id', true)")
        antes = cursor.fetchone()

    assert recuperar_eventos_cobranca.run(limite=10) == 1

    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('role', true), current_setting('rls.tenant_id', true)")
        depois = cursor.fetchone()
    assert depois == antes
    assert enviados == [(evento_id, "stripe", organizacao.pk)]
    with organizacao_atual_privilegiada(organizacao.pk):
        evento = EventoCobranca.objects.get(pk=evento_id)
    assert evento.status == StatusEventoCobranca.ROTEADO
    assert evento.organizacao_id == organizacao.pk


@pytest.mark.django_db(transaction=True)
def test_recovery_sem_destino_mantem_recebido_sem_efeitos(monkeypatch):
    evento_id = _evento_recebido_sem_tenant(identificador_assinatura="sub_desconhecida")
    enviados = []
    monkeypatch.setattr(
        "apps.assinaturas.subapps.faturamento.tasks.processar_evento_cobranca.delay",
        lambda *args: enviados.append(args),
    )

    assert recuperar_eventos_cobranca.run(limite=10) == 0
    assert enviados == []
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute("SET LOCAL ROLE billing_functions_owner")
        cursor.execute("SELECT status,organizacao_id,tentativas_roteamento,proxima_tentativa_em FROM evento_cobranca WHERE id=%s", [evento_id])
        status, organizacao_id, tentativas, proxima = cursor.fetchone()
        assert (status, organizacao_id, tentativas) == (StatusEventoCobranca.RECEBIDO, None, 1)
        assert proxima is not None
