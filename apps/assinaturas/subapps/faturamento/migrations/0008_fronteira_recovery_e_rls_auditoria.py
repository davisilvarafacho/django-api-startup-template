from django.conf import settings
from django.db import migrations


SQL = r"""
ALTER TABLE public.reabertura_evento_cobranca ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.reabertura_evento_cobranca FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS isolamento_organizacao ON public.reabertura_evento_cobranca;
CREATE POLICY isolamento_organizacao ON public.reabertura_evento_cobranca
USING (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer)
WITH CHECK (organizacao_id = NULLIF(current_setting('rls.tenant_id', true), '')::integer);

CREATE FUNCTION public.faturamento_claim_recovery(integer,timestamptz)
RETURNS TABLE(evento_id bigint,variante varchar(50),organizacao_id bigint)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
DECLARE item record; destinos bigint[]; destino bigint;
BEGIN
 IF NULLIF(current_setting('role',true),'') IS DISTINCT FROM 'billing_ingress_runtime'
 OR NULLIF(current_setting('rls.tenant_id',true),'')::integer IS DISTINCT FROM 0
 OR NULLIF(current_setting('rls.billing_ingress',true),'') IS DISTINCT FROM '1' THEN
  RAISE EXCEPTION 'papel e contexto operacionais obrigatorios' USING ERRCODE='42501'; END IF;
 IF $1 IS NULL OR $1 NOT BETWEEN 1 AND 1000 OR $2 IS NULL THEN
  RAISE EXCEPTION 'parametros de recovery invalidos' USING ERRCODE='22023'; END IF;
 FOR item IN
   SELECT e.id,e.variante,e.organizacao_id,e.identificador_assinatura,e.identificador_checkout,e.status,e.tentativas_roteamento
   FROM public.evento_cobranca e
   WHERE e.status IN (10,20,30) AND (e.proxima_tentativa_em IS NULL OR e.proxima_tentativa_em <= $2)
     AND (e.organizacao_id IS NOT NULL OR e.tentativas_roteamento < 8)
   ORDER BY COALESCE(e.proxima_tentativa_em,e.ocorrido_em),e.id
   FOR UPDATE SKIP LOCKED LIMIT $1
 LOOP
   destino := item.organizacao_id;
   IF destino IS NULL THEN
     SELECT array_agg(DISTINCT d.organizacao_id) INTO destinos FROM (
       SELECT g.organizacao_id FROM public.assinatura_gateway g
       WHERE g.variante=item.variante AND g.identificador_externo=item.identificador_assinatura
         AND g.is_active AND NOT g.is_deleted
       UNION ALL
       SELECT c.organizacao_id FROM public.checkout_cobranca c
       WHERE c.variante=item.variante AND c.identificador_externo=item.identificador_checkout
         AND c.is_active AND NOT c.is_deleted
     ) d;
     IF cardinality(destinos)=1 THEN destino:=destinos[1]; END IF;
     UPDATE public.evento_cobranca SET tentativas_roteamento=tentativas_roteamento+1,
       organizacao_id=destino,status=CASE WHEN destino IS NULL THEN 10 ELSE 20 END,
       proxima_tentativa_em=CASE WHEN destino IS NULL THEN $2 + make_interval(secs => LEAST(30 * (2 ^ item.tentativas_roteamento)::integer,3600)) ELSE $2 END,
       last_modified_at=statement_timestamp()
     WHERE id=item.id;
   END IF;
   IF destino IS NOT NULL THEN
     evento_id:=item.id; variante:=item.variante; organizacao_id:=destino; RETURN NEXT;
   END IF;
 END LOOP;
END;
$$;
REVOKE ALL ON FUNCTION public.faturamento_claim_recovery(integer,timestamptz) FROM PUBLIC;
ALTER FUNCTION public.faturamento_claim_recovery(integer,timestamptz) OWNER TO __BILLING_OWNER__;
GRANT EXECUTE ON FUNCTION public.faturamento_claim_recovery(integer,timestamptz) TO billing_ingress_runtime;
"""

REVERSE_SQL = r"""
REVOKE ALL ON FUNCTION public.faturamento_claim_recovery(integer,timestamptz) FROM billing_ingress_runtime;
DROP FUNCTION public.faturamento_claim_recovery(integer,timestamptz);
DROP POLICY IF EXISTS isolamento_organizacao ON public.reabertura_evento_cobranca;
ALTER TABLE public.reabertura_evento_cobranca NO FORCE ROW LEVEL SECURITY;
ALTER TABLE public.reabertura_evento_cobranca DISABLE ROW LEVEL SECURITY;
"""


def instalar(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        owner = settings.BILLING_DATABASE_OWNER_ROLE
        if owner != "billing_functions_owner":
            raise RuntimeError("Owner financeiro incompatível com o contrato operacional.")
        schema_editor.execute(SQL.replace("__BILLING_OWNER__", owner))


def remover(apps, schema_editor):
    del apps
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REVERSE_SQL)


class Migration(migrations.Migration):
    dependencies = [("faturamento", "0007_recovery_operacional")]
    operations = [migrations.RunPython(instalar, remover)]
