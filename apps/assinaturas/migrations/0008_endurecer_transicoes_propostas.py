from django.db import migrations, models


def condicao_estado_datas_proposta():
    sem_ativacao_ou_terminal = models.Q(
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    )
    rascunho = models.Q(
        status=10,
        enviada_em__isnull=True,
        aceita_em__isnull=True,
        aceita_por__isnull=True,
    ) & sem_ativacao_ou_terminal
    enviada = models.Q(
        status=20,
        enviada_em__isnull=False,
        enviada_em__lt=models.F("valida_ate"),
        aceita_em__isnull=True,
        aceita_por__isnull=True,
    ) & sem_ativacao_ou_terminal
    aceita = (
        models.Q(
            status=30,
            enviada_em__isnull=False,
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lt=models.F("valida_ate"),
        )
        & sem_ativacao_ou_terminal
    )
    ativada = models.Q(
        status=40,
        modo_ativacao=20,
        enviada_em__isnull=False,
        aceita_em__isnull=False,
        aceita_por__isnull=False,
        ativada_em__isnull=False,
        ativada_por__isnull=False,
        enviada_em__lte=models.F("aceita_em"),
        aceita_em__lte=models.F("ativada_em"),
        ativada_em__lt=models.F("valida_ate"),
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    ) & ~models.Q(justificativa_ativacao="")
    recusada = models.Q(
        status=50,
        enviada_em__isnull=False,
        aceita_em__isnull=True,
        aceita_por__isnull=True,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=False,
        recusada_por__isnull=False,
        enviada_em__lte=models.F("recusada_em"),
        recusada_em__lt=models.F("valida_ate"),
        expirada_em__isnull=True,
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    )
    expirada = models.Q(
        status=60,
        enviada_em__isnull=False,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=False,
        enviada_em__lte=models.F("expirada_em"),
        valida_ate__lte=models.F("expirada_em"),
        cancelada_em__isnull=True,
        cancelada_por__isnull=True,
    ) & (
        models.Q(aceita_em__isnull=True, aceita_por__isnull=True)
        | models.Q(
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lte=models.F("expirada_em"),
        )
    )
    cancelada = models.Q(
        status=70,
        ativada_em__isnull=True,
        ativada_por__isnull=True,
        justificativa_ativacao="",
        recusada_em__isnull=True,
        recusada_por__isnull=True,
        expirada_em__isnull=True,
        cancelada_em__isnull=False,
        cancelada_por__isnull=False,
    ) & (
        models.Q(enviada_em__isnull=True, aceita_em__isnull=True, aceita_por__isnull=True)
        | models.Q(
            enviada_em__isnull=False,
            enviada_em__lte=models.F("cancelada_em"),
            aceita_em__isnull=True,
            aceita_por__isnull=True,
        )
        | models.Q(
            enviada_em__isnull=False,
            aceita_em__isnull=False,
            aceita_por__isnull=False,
            enviada_em__lte=models.F("aceita_em"),
            aceita_em__lte=models.F("cancelada_em"),
        )
    )
    return rascunho | enviada | aceita | ativada | recusada | expirada | cancelada


CRIAR_GUARDA_TRANSICOES_PROPOSTA = """
CREATE OR REPLACE FUNCTION proteger_snapshot_proposta_comercial()
RETURNS trigger AS $$
DECLARE
    termos_mudaram boolean;
    metadados_mudaram boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Os termos e a identidade da proposta são imutáveis.';
    END IF;

    IF (
        NEW.id,
        NEW.organizacao_id,
        NEW.created_at,
        NEW.created_by_id
    ) IS DISTINCT FROM (
        OLD.id,
        OLD.organizacao_id,
        OLD.created_at,
        OLD.created_by_id
    ) THEN
        RAISE EXCEPTION 'Os termos e a identidade da proposta são imutáveis.';
    END IF;

    termos_mudaram := (
        to_jsonb(NEW) - ARRAY[
            'status', 'revisao', 'enviada_em', 'aceita_em', 'aceita_por_id',
            'ativada_em', 'ativada_por_id', 'justificativa_ativacao',
            'recusada_em', 'recusada_por_id', 'expirada_em', 'cancelada_em',
            'cancelada_por_id', 'is_active', 'last_modified_at'
        ]
    ) IS DISTINCT FROM (
        to_jsonb(OLD) - ARRAY[
            'status', 'revisao', 'enviada_em', 'aceita_em', 'aceita_por_id',
            'ativada_em', 'ativada_por_id', 'justificativa_ativacao',
            'recusada_em', 'recusada_por_id', 'expirada_em', 'cancelada_em',
            'cancelada_por_id', 'is_active', 'last_modified_at'
        ]
    );
    metadados_mudaram := (
        NEW.enviada_em, NEW.aceita_em, NEW.aceita_por_id,
        NEW.ativada_em, NEW.ativada_por_id, NEW.justificativa_ativacao,
        NEW.recusada_em, NEW.recusada_por_id, NEW.expirada_em,
        NEW.cancelada_em, NEW.cancelada_por_id, NEW.is_active
    ) IS DISTINCT FROM (
        OLD.enviada_em, OLD.aceita_em, OLD.aceita_por_id,
        OLD.ativada_em, OLD.ativada_por_id, OLD.justificativa_ativacao,
        OLD.recusada_em, OLD.recusada_por_id, OLD.expirada_em,
        OLD.cancelada_em, OLD.cancelada_por_id, OLD.is_active
    );

    IF (OLD.status <> 10 OR NEW.status <> 10) AND termos_mudaram THEN
        RAISE EXCEPTION 'Os termos de uma proposta enviada são imutáveis.';
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
            (OLD.status = 10 AND NEW.status IN (20, 70)) OR
            (OLD.status = 20 AND NEW.status IN (30, 50, 60, 70)) OR
            (OLD.status = 30 AND NEW.status IN (40, 60, 70))
        ) THEN
            RAISE EXCEPTION 'Transição inválida de status da proposta.' USING ERRCODE = '23514';
        END IF;
        IF NEW.revisao <> OLD.revisao + 1 THEN
            RAISE EXCEPTION 'A revisão da proposta deve avançar exatamente uma unidade.' USING ERRCODE = '23514';
        END IF;
    ELSE
        IF metadados_mudaram THEN
            RAISE EXCEPTION 'Metadados de transição só podem mudar com o status.' USING ERRCODE = '23514';
        END IF;
        IF termos_mudaram THEN
            IF OLD.status <> 10 OR NEW.revisao <> OLD.revisao + 1 THEN
                RAISE EXCEPTION 'A revisão do rascunho deve avançar exatamente uma unidade.' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.revisao IS DISTINCT FROM OLD.revisao THEN
            RAISE EXCEPTION 'A revisão não pode mudar sem uma transição nominal.' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

RESTAURAR_GUARDA_0007 = """
CREATE OR REPLACE FUNCTION proteger_snapshot_proposta_comercial()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Os termos e a identidade da proposta são imutáveis.';
    END IF;
    IF (
        NEW.id, NEW.organizacao_id, NEW.created_at, NEW.created_by_id
    ) IS DISTINCT FROM (
        OLD.id, OLD.organizacao_id, OLD.created_at, OLD.created_by_id
    ) THEN
        RAISE EXCEPTION 'Os termos e a identidade da proposta são imutáveis.';
    END IF;
    IF (OLD.status <> 10 OR NEW.status <> 10) AND (
        to_jsonb(NEW) - ARRAY[
            'status', 'revisao', 'enviada_em', 'aceita_em', 'aceita_por_id',
            'ativada_em', 'ativada_por_id', 'justificativa_ativacao',
            'recusada_em', 'recusada_por_id', 'expirada_em', 'cancelada_em',
            'cancelada_por_id', 'is_active', 'last_modified_at'
        ]
    ) IS DISTINCT FROM (
        to_jsonb(OLD) - ARRAY[
            'status', 'revisao', 'enviada_em', 'aceita_em', 'aceita_por_id',
            'ativada_em', 'ativada_por_id', 'justificativa_ativacao',
            'recusada_em', 'recusada_por_id', 'expirada_em', 'cancelada_em',
            'cancelada_por_id', 'is_active', 'last_modified_at'
        ]
    ) THEN
        RAISE EXCEPTION 'Os termos de uma proposta enviada são imutáveis.';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def criar_guarda_transicoes_proposta(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CRIAR_GUARDA_TRANSICOES_PROPOSTA)


def restaurar_guarda_0007(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(RESTAURAR_GUARDA_0007)


class Migration(migrations.Migration):
    dependencies = [("assinaturas", "0007_propostas_comerciais_enterprise")]

    operations = [
        migrations.RemoveConstraint(
            model_name="propostacomercial",
            name="proposta_estado_datas_coerente",
        ),
        migrations.AddConstraint(
            model_name="propostacomercial",
            constraint=models.CheckConstraint(
                condition=condicao_estado_datas_proposta(),
                name="proposta_estado_datas_coerente",
            ),
        ),
        migrations.RunPython(criar_guarda_transicoes_proposta, restaurar_guarda_0007),
    ]
