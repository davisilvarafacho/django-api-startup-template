from django.db import migrations


CRIAR_SELECTOR_OPERACIONAL_POSTGRESQL = r"""
CREATE OR REPLACE FUNCTION public.selecionar_organizacoes_operacionais_assinatura(
    finalidade text,
    apos_id bigint,
    limite integer,
    referencia timestamp with time zone
)
RETURNS TABLE (organizacao_id bigint)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    contexto_anterior text;
    id_atual bigint;
    assinatura_encontrada boolean;
    status_atual smallint;
    politica_trial_atual smallint;
    trial_termina_em_atual timestamp with time zone;
    seats_contratados_atual smallint;
    recursos_atuais jsonb;
    carencia_seats_iniciada_em_atual timestamp with time zone;
    consumidos_atuais bigint;
    devolvidas integer := 0;
    elegivel boolean;
BEGIN
    IF finalidade NOT IN ('rollout', 'trial_expired', 'seat_grace_mismatch') THEN
        RAISE EXCEPTION 'Finalidade operacional de assinatura inválida.' USING ERRCODE = '22023';
    END IF;
    IF apos_id < 0 OR limite <= 0 OR referencia IS NULL THEN
        RAISE EXCEPTION 'Cursor, limite ou referência operacional inválidos.' USING ERRCODE = '22023';
    END IF;

    contexto_anterior := pg_catalog.current_setting('rls.tenant_id', true);
    BEGIN
        FOR id_atual IN
            SELECT organizacao.id
              FROM public.organizacao AS organizacao
             WHERE organizacao.id > apos_id
               AND organizacao.is_active = TRUE
               AND organizacao.is_deleted = FALSE
             ORDER BY organizacao.id
        LOOP
            PERFORM pg_catalog.set_config('rls.tenant_id', id_atual::text, true);

            SELECT TRUE,
                   assinatura.status,
                   assinatura.politica_trial,
                   assinatura.trial_termina_em,
                   assinatura.seats_contratados,
                   assinatura.recursos,
                   assinatura.carencia_excesso_seats_iniciada_em
              INTO assinatura_encontrada,
                   status_atual,
                   politica_trial_atual,
                   trial_termina_em_atual,
                   seats_contratados_atual,
                   recursos_atuais,
                   carencia_seats_iniciada_em_atual
              FROM public.assinatura_organizacao AS assinatura
             WHERE assinatura.organizacao_id = id_atual
               AND assinatura.status IN (10, 20, 30)
             ORDER BY assinatura.id
             LIMIT 1;
            assinatura_encontrada := COALESCE(assinatura_encontrada, FALSE);
            elegivel := FALSE;

            IF finalidade = 'rollout' THEN
                elegivel := NOT assinatura_encontrada;
            ELSIF finalidade = 'trial_expired' THEN
                elegivel := assinatura_encontrada
                    AND status_atual = 20
                    AND politica_trial_atual = 10
                    AND trial_termina_em_atual IS NOT NULL
                    AND trial_termina_em_atual <= referencia;
            ELSIF assinatura_encontrada AND status_atual = 30 THEN
                SELECT pg_catalog.count(vinculo.id)
                  INTO consumidos_atuais
                  FROM public.vinculo AS vinculo
                 WHERE vinculo.organizacao_id = id_atual
                   AND vinculo.is_deleted = FALSE
                   AND NOT EXISTS (
                       SELECT 1
                         FROM pg_catalog.jsonb_array_elements_text(
                             COALESCE(recursos_atuais->'papeis_isentos_seat', '[]'::jsonb)
                         ) AS papel_isento(valor)
                        WHERE papel_isento.valor::smallint = vinculo.papel
                   );
                elegivel := (consumidos_atuais > seats_contratados_atual)
                    IS DISTINCT FROM (carencia_seats_iniciada_em_atual IS NOT NULL);
            END IF;

            IF elegivel THEN
                organizacao_id := id_atual;
                RETURN NEXT;
                devolvidas := devolvidas + 1;
                EXIT WHEN devolvidas >= limite;
            END IF;

            assinatura_encontrada := FALSE;
        END LOOP;
    EXCEPTION WHEN OTHERS THEN
        PERFORM pg_catalog.set_config('rls.tenant_id', COALESCE(contexto_anterior, ''), true);
        RAISE;
    END;
    PERFORM pg_catalog.set_config('rls.tenant_id', COALESCE(contexto_anterior, ''), true);
    RETURN;
END;
$$;

REVOKE ALL ON FUNCTION public.selecionar_organizacoes_operacionais_assinatura(
    text, bigint, integer, timestamp with time zone
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.selecionar_organizacoes_operacionais_assinatura(
    text, bigint, integer, timestamp with time zone
) TO CURRENT_USER;
"""


REMOVER_SELECTOR_OPERACIONAL_POSTGRESQL = r"""
DROP FUNCTION IF EXISTS public.selecionar_organizacoes_operacionais_assinatura(
    text, bigint, integer, timestamp with time zone
);
"""


ENDURECER_TRANSICOES_CONTRATUAIS_POSTGRESQL = r"""
CREATE OR REPLACE FUNCTION public.validar_transicao_status_assinatura()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog
AS $$
DECLARE
    mudou_termos boolean;
    alteracao_compativel boolean;
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

    IF (
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
    ) AND NEW.revisao <> OLD.revisao + 1 THEN
        RAISE EXCEPTION 'Alteracao contratual exige incremento unitario da revisao.' USING ERRCODE = '23514';
    END IF;

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


RESTAURAR_TRANSICOES_0011_POSTGRESQL = r"""
CREATE OR REPLACE FUNCTION public.validar_transicao_status_assinatura()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.status IS DISTINCT FROM NEW.status AND NOT (
            (OLD.status = 10 AND NEW.status IN (30, 40)) OR
            (OLD.status = 20 AND NEW.status IN (30, 40)) OR
            (OLD.status = 30 AND NEW.status = 40)
        ) THEN
            RAISE EXCEPTION 'Transição inválida de status da assinatura.' USING ERRCODE = '23514';
        END IF;

        IF (OLD.carencia_pagamento_iniciada_em, OLD.carencia_pagamento_termina_em)
           IS DISTINCT FROM (NEW.carencia_pagamento_iniciada_em, NEW.carencia_pagamento_termina_em)
           AND NEW.revisao <> OLD.revisao + 1 THEN
            RAISE EXCEPTION 'Alteracao da carencia financeira exige incremento unitario da revisao.' USING ERRCODE = '23514';
        END IF;

        IF (OLD.carencia_excesso_seats_iniciada_em, OLD.carencia_excesso_seats_termina_em)
           IS DISTINCT FROM (NEW.carencia_excesso_seats_iniciada_em, NEW.carencia_excesso_seats_termina_em)
           AND NEW.revisao <> OLD.revisao + 1 THEN
            RAISE EXCEPTION 'Alteracao da carencia de seats exige incremento unitario da revisao.' USING ERRCODE = '23514';
        END IF;
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
$$ LANGUAGE plpgsql;
"""


def criar_selector_operacional(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CRIAR_SELECTOR_OPERACIONAL_POSTGRESQL)
        schema_editor.execute(ENDURECER_TRANSICOES_CONTRATUAIS_POSTGRESQL)


def remover_selector_operacional(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(RESTAURAR_TRANSICOES_0011_POSTGRESQL)
        schema_editor.execute(REMOVER_SELECTOR_OPERACIONAL_POSTGRESQL)


class Migration(migrations.Migration):
    dependencies = [
        ("assinaturas", "0011_proteger_transicoes_acesso"),
    ]

    operations = [
        migrations.RunPython(criar_selector_operacional, remover_selector_operacional),
    ]
