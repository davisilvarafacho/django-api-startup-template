from django.db import migrations


SQL = r"""
DROP FUNCTION IF EXISTS public.faturamento_rotear_evento(bigint, bigint);

GRANT USAGE ON SCHEMA public TO billing_ingress_runtime;
GRANT SELECT, INSERT, UPDATE ON public.evento_cobranca TO billing_ingress_runtime;
GRANT SELECT ON public.assinatura_gateway TO billing_ingress_runtime;
GRANT USAGE, SELECT ON SEQUENCE public.evento_cobranca_id_seq TO billing_ingress_runtime;

DROP POLICY evento_ingresso_select ON public.evento_cobranca;
DROP POLICY evento_ingresso_insert ON public.evento_cobranca;
DROP POLICY evento_ingresso_update ON public.evento_cobranca;
CREATE POLICY evento_ingresso_select ON public.evento_cobranca FOR SELECT TO billing_ingress_runtime USING (
    ((organizacao_id IS NULL) OR id = NULLIF(current_setting('rls.billing_routing_event_id', true), '')::bigint)
    AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
    AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1'
);
CREATE POLICY evento_ingresso_insert ON public.evento_cobranca FOR INSERT TO billing_ingress_runtime WITH CHECK (
    organizacao_id IS NULL
    AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
    AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1'
);
CREATE POLICY evento_ingresso_update ON public.evento_cobranca FOR UPDATE TO billing_ingress_runtime
USING (
    organizacao_id IS NULL
    AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
    AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1'
)
WITH CHECK (
    id = NULLIF(current_setting('rls.billing_routing_event_id', true), '')::bigint
    AND organizacao_id = (
        SELECT ag.organizacao_id FROM public.assinatura_gateway ag
        WHERE ag.variante = evento_cobranca.variante
          AND ag.identificador_externo = evento_cobranca.identificador_assinatura
          AND ag.is_active AND NOT ag.is_deleted
    )
);

CREATE FUNCTION public.faturamento_rotear_evento(bigint)
RETURNS boolean LANGUAGE plpgsql SECURITY INVOKER
SET search_path = pg_catalog, public, pg_temp AS $$
DECLARE alteradas integer;
BEGIN
    IF current_user IS DISTINCT FROM 'billing_ingress_runtime'
       OR NULLIF(current_setting('rls.tenant_id', true), '')::integer IS DISTINCT FROM 0
       OR NULLIF(current_setting('rls.billing_ingress', true), '') IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'papel e contexto de ingresso obrigatorios' USING ERRCODE = '42501';
    END IF;
    PERFORM set_config('rls.billing_routing_event_id', $1::text, true);
    UPDATE public.evento_cobranca SET
        organizacao_id = (
            SELECT ag.organizacao_id FROM public.assinatura_gateway ag
            WHERE ag.variante = evento_cobranca.variante
              AND ag.identificador_externo = evento_cobranca.identificador_assinatura
              AND ag.is_active AND NOT ag.is_deleted
        ),
        status = 20,
        last_modified_at = statement_timestamp()
    WHERE id = $1 AND organizacao_id IS NULL
      AND EXISTS (
          SELECT 1 FROM public.assinatura_gateway ag
          WHERE ag.variante = evento_cobranca.variante
            AND ag.identificador_externo = evento_cobranca.identificador_assinatura
            AND ag.is_active AND NOT ag.is_deleted
      );
    GET DIAGNOSTICS alteradas = ROW_COUNT;
    PERFORM set_config('rls.billing_routing_event_id', '', true);
    RETURN alteradas = 1;
EXCEPTION WHEN OTHERS THEN
    PERFORM set_config('rls.billing_routing_event_id', '', true);
    RAISE;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento(bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.faturamento_rotear_evento(bigint) TO billing_ingress_runtime;

CREATE OR REPLACE FUNCTION public.texto_timestamp_valido(text) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
BEGIN
 PERFORM $1::timestamptz;
 RETURN $1 ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-](0[0-9]|1[0-4]):[0-5][0-9])$';
EXCEPTION WHEN OTHERS THEN RETURN false;
END;
$$;
CREATE OR REPLACE FUNCTION public.evento_payload_valido(jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, pg_temp AS $$
SELECT jsonb_typeof($1) = 'object'
AND ARRAY(SELECT jsonb_object_keys($1)) <@ ARRAY[
 'amount','currency','customer_reference','failure_code','invoice_status',
 'payment_status','period_end','period_start','subscription_status']::text[]
AND NOT EXISTS (
 SELECT 1 FROM jsonb_each($1) item WHERE
 CASE item.key
  WHEN 'amount' THEN jsonb_typeof(item.value) <> 'number' OR (item.value #>> '{}') !~ '^[0-9]+$' OR (item.value #>> '{}')::numeric > 9223372036854775807
  WHEN 'currency' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[A-Z]{3}$'
  WHEN 'customer_reference' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[A-Za-z0-9_.:~-]{1,512}$'
  WHEN 'failure_code' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[A-Za-z0-9_.:-]{1,100}$'
  WHEN 'invoice_status' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[a-z][a-z0-9_.-]{0,49}$'
  WHEN 'payment_status' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[a-z][a-z0-9_.-]{0,49}$'
  WHEN 'subscription_status' THEN jsonb_typeof(item.value) <> 'string' OR (item.value #>> '{}') !~ '^[a-z][a-z0-9_.-]{0,49}$'
  WHEN 'period_start' THEN jsonb_typeof(item.value) <> 'string' OR NOT public.texto_timestamp_valido(item.value #>> '{}')
  WHEN 'period_end' THEN jsonb_typeof(item.value) <> 'string' OR NOT public.texto_timestamp_valido(item.value #>> '{}')
  ELSE true
 END
);
$$;
"""


REVERSE_SQL = """
DROP FUNCTION IF EXISTS public.faturamento_rotear_evento(bigint);
DROP POLICY IF EXISTS evento_ingresso_update ON public.evento_cobranca;
DROP POLICY IF EXISTS evento_ingresso_insert ON public.evento_cobranca;
DROP POLICY IF EXISTS evento_ingresso_select ON public.evento_cobranca;
REVOKE ALL ON public.evento_cobranca FROM billing_ingress_runtime;
REVOKE ALL ON public.assinatura_gateway FROM billing_ingress_runtime;
REVOKE ALL ON SEQUENCE public.evento_cobranca_id_seq FROM billing_ingress_runtime;
REVOKE USAGE ON SCHEMA public FROM billing_ingress_runtime;
DROP FUNCTION IF EXISTS public.texto_timestamp_valido(text);
CREATE OR REPLACE FUNCTION public.evento_payload_valido(jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT jsonb_typeof($1) = 'object'
    AND ARRAY(SELECT jsonb_object_keys($1)) <@ ARRAY[
        'amount', 'currency', 'customer_reference', 'failure_code',
        'invoice_status', 'payment_status', 'period_end', 'period_start',
        'subscription_status'
    ]::text[]
    AND NOT EXISTS (
        SELECT 1 FROM jsonb_each($1) item
        WHERE jsonb_typeof(item.value) NOT IN ('string', 'number', 'boolean', 'null')
    );
$$;
CREATE POLICY evento_ingresso_select ON public.evento_cobranca FOR SELECT USING (
 organizacao_id IS NULL AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
 AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1');
CREATE POLICY evento_ingresso_insert ON public.evento_cobranca FOR INSERT WITH CHECK (
 organizacao_id IS NULL AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
 AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1');
CREATE POLICY evento_ingresso_update ON public.evento_cobranca FOR UPDATE USING (
 organizacao_id IS NULL AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
 AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1') WITH CHECK (
 organizacao_id IS NOT NULL AND NULLIF(current_setting('rls.tenant_id', true), '')::integer = 0
 AND NULLIF(current_setting('rls.billing_ingress', true), '') = '1');
CREATE FUNCTION public.faturamento_rotear_evento(bigint, bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, pg_temp AS $$
DECLARE alteradas integer;
BEGIN
    IF NULLIF(current_setting('rls.tenant_id', true), '')::integer IS DISTINCT FROM 0
       OR NULLIF(current_setting('rls.billing_ingress', true), '') IS DISTINCT FROM '1' THEN
        RAISE EXCEPTION 'contexto de ingresso obrigatorio' USING ERRCODE = '42501';
    END IF;
    UPDATE evento_cobranca SET organizacao_id = $2, status = 20, last_modified_at = NOW()
    WHERE id = $1 AND organizacao_id IS NULL;
    GET DIAGNOSTICS alteradas = ROW_COUNT;
    RETURN alteradas = 1;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento(bigint,bigint) FROM PUBLIC;
"""


def aplicar(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)


def reverter(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0002_alter_eventocobranca_payload_normalizado")]
    operations = [migrations.RunPython(aplicar, reverter)]
