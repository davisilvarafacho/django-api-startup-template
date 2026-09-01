from django.conf import settings
from django.db import migrations


SQL = r"""
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento(text,text) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_rotear_evento(text,text);
REVOKE ALL ON FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz);
CREATE OR REPLACE FUNCTION public.evento_payload_valido(jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT jsonb_typeof($1)='object'
AND ARRAY(SELECT jsonb_object_keys($1)) <@ ARRAY[
 'amount','currency','customer_reference','failure_code','invoice_status',
 'payment_status','period_end','period_start','subscription_status']::text[]
AND NOT EXISTS (SELECT 1 FROM jsonb_each($1) item WHERE CASE item.key
 WHEN 'amount' THEN jsonb_typeof(item.value)<>'number' OR (item.value#>>'{}')!~'^[0-9]+$' OR (item.value#>>'{}')::numeric>9223372036854775807
 WHEN 'currency' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[A-Z]{3}$'
 WHEN 'customer_reference' THEN jsonb_typeof(item.value)<>'string' OR length(item.value#>>'{}') NOT BETWEEN 1 AND 512
   OR (item.value#>>'{}')!~'^[A-Za-z0-9_.:~-]+$'
 WHEN 'failure_code' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[A-Za-z0-9_.:-]{1,100}$'
 WHEN 'invoice_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'payment_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'subscription_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'period_start' THEN jsonb_typeof(item.value)<>'string' OR NOT public.texto_timestamp_valido(item.value#>>'{}')
 WHEN 'period_end' THEN jsonb_typeof(item.value)<>'string' OR NOT public.texto_timestamp_valido(item.value#>>'{}')
 ELSE true END);
$$;
CREATE FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)
RETURNS TABLE(evento_id bigint, criado boolean, hash_payload varchar(64), organizacao_id bigint, status smallint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE inseridas integer;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto de ingresso obrigatorios' USING ERRCODE='42501'; END IF;
 IF $1 IS NULL OR length($1) NOT BETWEEN 1 AND 50 OR $2 IS NULL OR length($2) NOT BETWEEN 1 AND 255
 OR $3 IS NULL OR length($3) NOT BETWEEN 1 AND 100 OR $3!~'^[a-z][a-z0-9]*([._][a-z0-9]+)*$' OR $3~'[^a-z0-9._]'
 OR length(COALESCE($4,''))>255 OR length(COALESCE($5,''))>255 OR length(COALESCE($6,''))>255 OR $7 IS NULL
 OR $8 IS NULL OR NOT public.evento_payload_valido($8) OR $9 IS NULL OR $9!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'evento normalizado invalido' USING ERRCODE='22023'; END IF;
 INSERT INTO public.evento_cobranca(created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,
  identificador_assinatura,identificador_checkout,identificador_fatura,status,exige_tenant,tentativas_roteamento,
  tentativas_processamento,payload_normalizado,hash_payload,erro,ocorrido_em)
 VALUES(statement_timestamp(),statement_timestamp(),true,false,$1,$2,$3,COALESCE($4,''),COALESCE($5,''),COALESCE($6,''),
  CASE WHEN $7 THEN 10 ELSE 50 END,$7,0,0,$8,$9,'',$10)
 ON CONFLICT(variante,identificador_evento) DO NOTHING;
 GET DIAGNOSTICS inseridas=ROW_COUNT;
 RETURN QUERY SELECT e.id,inseridas=1,e.hash_payload,e.organizacao_id,e.status
 FROM public.evento_cobranca e WHERE e.variante=$1 AND e.identificador_evento=$2 LIMIT 1;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) FROM PUBLIC;
ALTER FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) TO billing_ingress_runtime;

CREATE FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE alteradas integer;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto de ingresso obrigatorios' USING ERRCODE='42501'; END IF;
 IF $1 IS NULL OR $1 < 1 OR $2 IS NULL OR $2 < 1 THEN
  RAISE EXCEPTION 'destino de evento invalido' USING ERRCODE='22023'; END IF;
 UPDATE public.evento_cobranca SET organizacao_id=$2,status=20,last_modified_at=statement_timestamp()
 WHERE id=$1 AND organizacao_id IS NULL AND status=10 AND exige_tenant;
 GET DIAGNOSTICS alteradas=ROW_COUNT; RETURN alteradas=1;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint) FROM PUBLIC;
ALTER FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint) TO billing_ingress_runtime;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_rotear_evento_destino(bigint,bigint);
REVOKE ALL ON FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_ingress_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz);
CREATE FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)
RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE inseridas integer;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto de ingresso obrigatorios' USING ERRCODE='42501'; END IF;
 IF $1 IS NULL OR length($1) NOT BETWEEN 1 AND 50 OR $2 IS NULL OR length($2) NOT BETWEEN 1 AND 255
 OR $3 IS NULL OR length($3) NOT BETWEEN 1 AND 100 OR $3!~'^[a-z][a-z0-9]*([._][a-z0-9]+)*$' OR $3~'[^a-z0-9._]'
 OR length(COALESCE($4,''))>255 OR length(COALESCE($5,''))>255 OR length(COALESCE($6,''))>255 OR $7 IS NULL
 OR $8 IS NULL OR NOT public.evento_payload_valido($8) OR $9 IS NULL OR $9!~'^[0-9a-f]{64}$' THEN
  RAISE EXCEPTION 'evento normalizado invalido' USING ERRCODE='22023'; END IF;
 INSERT INTO public.evento_cobranca(created_at,last_modified_at,is_active,is_deleted,variante,identificador_evento,tipo,
 identificador_assinatura,identificador_checkout,identificador_fatura,status,exige_tenant,tentativas_roteamento,
 tentativas_processamento,payload_normalizado,hash_payload,erro,ocorrido_em)
 VALUES(statement_timestamp(),statement_timestamp(),true,false,$1,$2,$3,COALESCE($4,''),COALESCE($5,''),COALESCE($6,''),10,$7,0,0,$8,$9,'',$10)
 ON CONFLICT(variante,identificador_evento) DO NOTHING;
 GET DIAGNOSTICS inseridas=ROW_COUNT; RETURN inseridas=1;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) FROM PUBLIC;
ALTER FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz) TO billing_ingress_runtime;
CREATE FUNCTION public.faturamento_rotear_evento(text,text) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE alteradas integer;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto de ingresso obrigatorios' USING ERRCODE='42501'; END IF;
 UPDATE public.evento_cobranca e SET organizacao_id=g.organizacao_id,status=20,last_modified_at=statement_timestamp()
 FROM public.assinatura_gateway g WHERE e.variante=$1 AND e.identificador_evento=$2 AND e.organizacao_id IS NULL
 AND g.variante=e.variante AND g.identificador_externo=e.identificador_assinatura AND g.is_active AND NOT g.is_deleted;
 GET DIAGNOSTICS alteradas=ROW_COUNT; RETURN alteradas=1;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_rotear_evento(text,text) FROM PUBLIC;
ALTER FUNCTION public.faturamento_rotear_evento(text,text) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_rotear_evento(text,text) TO billing_ingress_runtime;
CREATE OR REPLACE FUNCTION public.evento_payload_valido(jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT SECURITY INVOKER SET search_path=pg_catalog,pg_temp AS $$
SELECT jsonb_typeof($1)='object'
AND ARRAY(SELECT jsonb_object_keys($1)) <@ ARRAY[
 'amount','currency','customer_reference','failure_code','invoice_status',
 'payment_status','period_end','period_start','subscription_status']::text[]
AND NOT EXISTS (SELECT 1 FROM jsonb_each($1) item WHERE CASE item.key
 WHEN 'amount' THEN jsonb_typeof(item.value)<>'number' OR (item.value#>>'{}')!~'^[0-9]+$' OR (item.value#>>'{}')::numeric>9223372036854775807
 WHEN 'currency' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[A-Z]{3}$'
 WHEN 'customer_reference' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[A-Za-z0-9_.:~-]{1,512}$'
 WHEN 'failure_code' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[A-Za-z0-9_.:-]{1,100}$'
 WHEN 'invoice_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'payment_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'subscription_status' THEN jsonb_typeof(item.value)<>'string' OR (item.value#>>'{}')!~'^[a-z][a-z0-9_.-]{0,49}$'
 WHEN 'period_start' THEN jsonb_typeof(item.value)<>'string' OR NOT public.texto_timestamp_valido(item.value#>>'{}')
 WHEN 'period_end' THEN jsonb_typeof(item.value)<>'string' OR NOT public.texto_timestamp_valido(item.value#>>'{}')
 ELSE true END);
$$;
"""


def instalar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    owner = settings.BILLING_DATABASE_OWNER_ROLE
    if owner != "billing_functions_owner":
        raise RuntimeError("O owner de faturamento deve corresponder ao contrato das funções instaladas.")
    schema_editor.execute(SQL.replace("__BILLING_OWNER__", owner))


def preflight(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor != "postgresql":
        return
    owner = settings.BILLING_DATABASE_OWNER_ROLE
    runtime = settings.BILLING_INGRESS_DATABASE_ROLE
    if owner != "billing_functions_owner" or runtime != "billing_ingress_runtime" or owner == runtime:
        raise RuntimeError("As roles de faturamento não correspondem ao contrato instalado.")
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """SELECT rolname,rolcanlogin,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolinherit,rolreplication,
                      pg_has_role(current_user,oid,'SET'), EXISTS(SELECT 1 FROM pg_auth_members WHERE member=pg_roles.oid)
               FROM pg_roles WHERE rolname IN (%s,%s) ORDER BY rolname""",
            [owner, runtime],
        )
        roles = cursor.fetchall()
    if len(roles) != 2 or any(row[1:8] != (False, False, False, False, False, False, False) or row[9] for row in roles):
        raise RuntimeError("As roles de faturamento não existem ou possuem atributos/memberships inseguros.")
    memberships = {row[0]: row[8] for row in roles}
    if not memberships[owner]:
        raise RuntimeError("A credencial DDL deve poder executar SET ROLE para o owner de faturamento.")


def remover(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        owner = settings.BILLING_DATABASE_OWNER_ROLE
        schema_editor.execute(REVERSE_SQL.replace("__BILLING_OWNER__", owner))


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0003_checkout_erro_codigo")]
    operations = [migrations.RunPython(preflight, migrations.RunPython.noop), migrations.RunPython(instalar, remover)]
