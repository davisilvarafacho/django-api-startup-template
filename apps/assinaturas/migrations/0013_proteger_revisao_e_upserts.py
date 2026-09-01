from django.db import migrations


TRANSICOES_CONTRATUAIS_TEMPLATE_POSTGRESQL = r"""
CREATE OR REPLACE FUNCTION public.validar_transicao_status_assinatura()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    mudou_termos boolean;
    alteracao_compativel boolean;
__DECLARACAO_MUDANCA__
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status_financeiro IN (40, 50) AND (
            NEW.carencia_pagamento_iniciada_em IS NULL OR NEW.carencia_pagamento_termina_em IS NULL
        ) THEN
            RAISE EXCEPTION 'Status financeiro irregular exige carencia financeira completa.' USING ERRCODE = '23514';
        ELSIF NEW.status_financeiro NOT IN (40, 50) AND (
            NEW.carencia_pagamento_iniciada_em IS NOT NULL OR NEW.carencia_pagamento_termina_em IS NOT NULL
        ) THEN
            RAISE EXCEPTION 'Status financeiro regular nao admite carencia financeira.' USING ERRCODE = '23514';
        END IF;
        IF NEW.carencia_pagamento_iniciada_em IS NOT NULL AND (
            NEW.carencia_pagamento_termina_em IS DISTINCT FROM
            NEW.carencia_pagamento_iniciada_em + NEW.carencia_pagamento_dias * INTERVAL '1 day'
        ) THEN
            RAISE EXCEPTION 'Prazo da carencia financeira diverge do snapshot contratado.' USING ERRCODE = '23514';
        END IF;
        IF (NEW.carencia_excesso_seats_iniciada_em IS NULL) <> (NEW.carencia_excesso_seats_termina_em IS NULL) THEN
            RAISE EXCEPTION 'Carencia de seats exige inicio e termino juntos.' USING ERRCODE = '23514';
        END IF;
        IF NEW.carencia_excesso_seats_iniciada_em IS NOT NULL AND (
            NEW.carencia_excesso_seats_termina_em IS DISTINCT FROM
            NEW.carencia_excesso_seats_iniciada_em + NEW.carencia_excesso_seats_dias * INTERVAL '1 day'
        ) THEN
            RAISE EXCEPTION 'Prazo da carencia de seats diverge do snapshot contratado.' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
        (OLD.status = 10 AND NEW.status IN (30, 40)) OR
        (OLD.status = 20 AND NEW.status IN (30, 40)) OR
        (OLD.status = 30 AND NEW.status = 40)
    ) THEN
        RAISE EXCEPTION 'Transição inválida de status da assinatura.' USING ERRCODE = '23514';
    END IF;

__REGRA_REVISAO__

    IF OLD.status = 20 AND NEW.status = 30 AND NEW.status_financeiro = 30 AND (
        NEW.periodo_atual_iniciado_em IS NULL
        OR NEW.periodo_atual_termina_em IS NULL
        OR NEW.periodo_atual_iniciado_em >= NEW.periodo_atual_termina_em
        OR NEW.seats_contratados = 0
    ) THEN
        RAISE EXCEPTION 'Conversao de trial pago exige periodo e capacidade confirmados.' USING ERRCODE = '23514';
    END IF;

    mudou_termos := (
        OLD.versao_plano_id,
        OLD.proposta_comercial_id,
        OLD.periodicidade,
        OLD.moeda,
        OLD.valor_base_centavos,
        OLD.valor_seat_centavos,
        OLD.seats_inclusos,
        OLD.seats_contratados,
        OLD.expansao_automatica_seats,
        OLD.recursos,
        OLD.carencia_pagamento_dias,
        OLD.carencia_excesso_seats_dias
    ) IS DISTINCT FROM (
        NEW.versao_plano_id,
        NEW.proposta_comercial_id,
        NEW.periodicidade,
        NEW.moeda,
        NEW.valor_base_centavos,
        NEW.valor_seat_centavos,
        NEW.seats_inclusos,
        NEW.seats_contratados,
        NEW.expansao_automatica_seats,
        NEW.recursos,
        NEW.carencia_pagamento_dias,
        NEW.carencia_excesso_seats_dias
    );

    IF mudou_termos AND NOT (OLD.status = 20 AND NEW.status = 30 AND NEW.status_financeiro = 30) THEN
        SELECT COALESCE(pg_catalog.bool_or(
            alteracao.snapshot_anterior @> pg_catalog.jsonb_build_object(
                'revisao', OLD.revisao,
                'versao_plano_id', OLD.versao_plano_id,
                'proposta_comercial_id', OLD.proposta_comercial_id,
                'periodicidade', OLD.periodicidade,
                'moeda', OLD.moeda,
                'valor_base_centavos', OLD.valor_base_centavos,
                'valor_seat_centavos', OLD.valor_seat_centavos,
                'seats_inclusos', OLD.seats_inclusos,
                'seats_contratados', OLD.seats_contratados,
                'expansao_automatica_seats', OLD.expansao_automatica_seats,
                'recursos', OLD.recursos,
                'carencia_pagamento_dias', OLD.carencia_pagamento_dias,
                'carencia_excesso_seats_dias', OLD.carencia_excesso_seats_dias
            )
            AND alteracao.snapshot_pretendido @> pg_catalog.jsonb_build_object(
                'revisao', NEW.revisao,
                'versao_plano_id', NEW.versao_plano_id,
                'proposta_comercial_id', NEW.proposta_comercial_id,
                'periodicidade', NEW.periodicidade,
                'moeda', NEW.moeda,
                'valor_base_centavos', NEW.valor_base_centavos,
                'valor_seat_centavos', NEW.valor_seat_centavos,
                'seats_inclusos', NEW.seats_inclusos,
                'seats_contratados', NEW.seats_contratados,
                'expansao_automatica_seats', NEW.expansao_automatica_seats,
                'recursos', NEW.recursos,
                'carencia_pagamento_dias', NEW.carencia_pagamento_dias,
                'carencia_excesso_seats_dias', NEW.carencia_excesso_seats_dias
            )
        ), FALSE)
          INTO alteracao_compativel
          FROM public.alteracao_assinatura AS alteracao
         WHERE alteracao.assinatura_id = NEW.id
           AND alteracao.revisao_esperada = OLD.revisao
           AND alteracao.status IN (10, 20, 30)
           AND alteracao.aplicada_em IS NULL
           AND alteracao.revisao_aplicada IS NULL;

        IF NOT alteracao_compativel THEN
            RAISE EXCEPTION 'Alteracao de origem ou termos exige AlteracaoAssinatura compativel.' USING ERRCODE = '23514';
        END IF;
    END IF;

    IF OLD.status = 20 AND NEW.status = 30 AND NEW.status_financeiro = 10 AND NOT alteracao_compativel THEN
        RAISE EXCEPTION 'Fallback de trial exige AlteracaoAssinatura compativel.' USING ERRCODE = '23514';
    END IF;

    IF NEW.status_financeiro IN (40, 50) THEN
        IF NEW.carencia_pagamento_iniciada_em IS NULL OR NEW.carencia_pagamento_termina_em IS NULL THEN
            RAISE EXCEPTION 'Status financeiro irregular exige carencia financeira completa.' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.carencia_pagamento_iniciada_em IS NOT NULL OR NEW.carencia_pagamento_termina_em IS NOT NULL THEN
        RAISE EXCEPTION 'Status financeiro regular nao admite carencia financeira.' USING ERRCODE = '23514';
    END IF;

    IF NEW.carencia_pagamento_iniciada_em IS NOT NULL AND (
        NEW.carencia_pagamento_termina_em IS DISTINCT FROM
        NEW.carencia_pagamento_iniciada_em + NEW.carencia_pagamento_dias * INTERVAL '1 day'
    ) THEN
        RAISE EXCEPTION 'Prazo da carencia financeira diverge do snapshot contratado.' USING ERRCODE = '23514';
    END IF;

    IF (NEW.carencia_excesso_seats_iniciada_em IS NULL) <> (NEW.carencia_excesso_seats_termina_em IS NULL) THEN
        RAISE EXCEPTION 'Carencia de seats exige inicio e termino juntos.' USING ERRCODE = '23514';
    END IF;

    IF NEW.carencia_excesso_seats_iniciada_em IS NOT NULL AND (
        NEW.carencia_excesso_seats_termina_em IS DISTINCT FROM
        NEW.carencia_excesso_seats_iniciada_em + NEW.carencia_excesso_seats_dias * INTERVAL '1 day'
    ) THEN
        RAISE EXCEPTION 'Prazo da carencia de seats diverge do snapshot contratado.' USING ERRCODE = '23514';
    END IF;

    RETURN NEW;
END;
$$;
"""


TUPLA_SENSIVEL_POSTGRESQL = r"""(
        OLD.versao_plano_id,
        OLD.proposta_comercial_id,
        OLD.status,
        OLD.status_financeiro,
        OLD.periodicidade,
        OLD.moeda,
        OLD.valor_base_centavos,
        OLD.valor_seat_centavos,
        OLD.seats_inclusos,
        OLD.seats_contratados,
        OLD.expansao_automatica_seats,
        OLD.recursos,
        OLD.politica_trial,
        OLD.trial_iniciado_em,
        OLD.trial_termina_em,
        OLD.periodo_atual_iniciado_em,
        OLD.periodo_atual_termina_em,
        OLD.carencia_pagamento_dias,
        OLD.carencia_pagamento_iniciada_em,
        OLD.carencia_pagamento_termina_em,
        OLD.carencia_excesso_seats_dias,
        OLD.carencia_excesso_seats_iniciada_em,
        OLD.carencia_excesso_seats_termina_em,
        OLD.cancelamento_agendado_para,
        OLD.encerrada_em,
        OLD.motivo_encerramento
    ) IS DISTINCT FROM (
        NEW.versao_plano_id,
        NEW.proposta_comercial_id,
        NEW.status,
        NEW.status_financeiro,
        NEW.periodicidade,
        NEW.moeda,
        NEW.valor_base_centavos,
        NEW.valor_seat_centavos,
        NEW.seats_inclusos,
        NEW.seats_contratados,
        NEW.expansao_automatica_seats,
        NEW.recursos,
        NEW.politica_trial,
        NEW.trial_iniciado_em,
        NEW.trial_termina_em,
        NEW.periodo_atual_iniciado_em,
        NEW.periodo_atual_termina_em,
        NEW.carencia_pagamento_dias,
        NEW.carencia_pagamento_iniciada_em,
        NEW.carencia_pagamento_termina_em,
        NEW.carencia_excesso_seats_dias,
        NEW.carencia_excesso_seats_iniciada_em,
        NEW.carencia_excesso_seats_termina_em,
        NEW.cancelamento_agendado_para,
        NEW.encerrada_em,
        NEW.motivo_encerramento
    )"""


REGRA_REVISAO_0012_POSTGRESQL = f"""    IF {TUPLA_SENSIVEL_POSTGRESQL} AND NEW.revisao <> OLD.revisao + 1 THEN
        RAISE EXCEPTION 'Alteracao contratual exige incremento unitario da revisao.' USING ERRCODE = '23514';
    END IF;"""


REGRA_REVISAO_0013_POSTGRESQL = f"""    mudou_transicao := {TUPLA_SENSIVEL_POSTGRESQL};

    IF mudou_transicao AND NEW.revisao IS DISTINCT FROM OLD.revisao + 1 THEN
        RAISE EXCEPTION 'Alteracao contratual exige incremento unitario da revisao.' USING ERRCODE = '23514';
    ELSIF NOT mudou_transicao AND NEW.revisao IS DISTINCT FROM OLD.revisao THEN
        RAISE EXCEPTION 'Revisao da assinatura nao pode mudar isoladamente.' USING ERRCODE = '23514';
    END IF;"""


TRANSICOES_0012_POSTGRESQL = TRANSICOES_CONTRATUAIS_TEMPLATE_POSTGRESQL.replace("__DECLARACAO_MUDANCA__\n", "").replace(
    "__REGRA_REVISAO__", REGRA_REVISAO_0012_POSTGRESQL
)
TRANSICOES_0013_POSTGRESQL = TRANSICOES_CONTRATUAIS_TEMPLATE_POSTGRESQL.replace(
    "__DECLARACAO_MUDANCA__", "    mudou_transicao boolean;"
).replace("__REGRA_REVISAO__", REGRA_REVISAO_0013_POSTGRESQL)


def proteger_revisao(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(TRANSICOES_0013_POSTGRESQL)


def restaurar_transicoes_0012(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(TRANSICOES_0012_POSTGRESQL)


class Migration(migrations.Migration):
    dependencies = [
        ("assinaturas", "0012_endurecer_rollout_e_transicoes"),
    ]

    operations = [
        migrations.RunPython(proteger_revisao, restaurar_transicoes_0012),
    ]
