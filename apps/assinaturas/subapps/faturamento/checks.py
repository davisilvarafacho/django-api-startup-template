from django.conf import settings
from django.core.checks import Error, Tags, register
from django.db import connection, connections


@register()
def configuracao_faturamento_check(app_configs, **kwargs):
    del app_configs, kwargs
    stripe = settings.CHECKOUT_VARIANTS.get("stripe")
    configuracao = stripe[1] if isinstance(stripe, (tuple, list)) and len(stripe) == 2 and isinstance(stripe[1], dict) else {}
    if not all(configuracao.get(key) for key in ("api_key", "webhook_secret")):
        return [Error("Credenciais Stripe não configuradas.", id="faturamento.E001")]
    return []


def _erro(message, error_id, hint):
    return [Error(message, hint=hint, id=error_id)]


@register(Tags.database, deploy=True)
def role_ingresso_check(app_configs, **kwargs):
    """Prova o grafo owner/migration/web/worker e o catálogo SECURITY DEFINER."""
    del app_configs
    runtime = settings.BILLING_INGRESS_DATABASE_ROLE
    owner = settings.BILLING_DATABASE_OWNER_ROLE
    modo = settings.BILLING_DATABASE_MODE
    if runtime != "billing_ingress_runtime" or owner != "billing_functions_owner" or runtime == owner:
        return _erro(
            "As roles de faturamento não correspondem ao contrato operacional.",
            "faturamento.E002",
            "Use roles fixas e distintas para runtime e owner.",
        )
    if modo not in {"web", "ingress", "migration"}:
        return _erro("BILLING_DATABASE_MODE inválido.", "faturamento.E005", "Use web, ingress ou migration.")
    aliases = kwargs.get("databases") or ("default",)
    conexao = connections[aliases[0]] if aliases[0] != "default" else connection
    if conexao.vendor != "postgresql":
        return []

    with conexao.cursor() as cursor:
        cursor.execute(
            """WITH RECURSIVE roles_alvo AS (
                   SELECT oid, rolname, rolcanlogin, rolsuper, rolbypassrls, rolcreaterole,
                          rolcreatedb, rolinherit, rolreplication
                   FROM pg_roles WHERE rolname IN (%s, %s)
               ), outbound(origem, destino, caminho, ciclo) AS (
                   SELECT alvo.oid, membro.roleid, ARRAY[alvo.oid, membro.roleid], membro.roleid=alvo.oid
                   FROM roles_alvo alvo JOIN pg_auth_members membro ON membro.member=alvo.oid
                   UNION ALL
                   SELECT outbound.origem, membro.roleid, outbound.caminho || membro.roleid,
                          membro.roleid=ANY(outbound.caminho)
                   FROM outbound JOIN pg_auth_members membro ON membro.member=outbound.destino
                   WHERE NOT outbound.ciclo
               )
               SELECT alvo.rolname, alvo.rolcanlogin, alvo.rolsuper, alvo.rolbypassrls,
                      alvo.rolcreaterole, alvo.rolcreatedb, alvo.rolinherit, alvo.rolreplication,
                      pg_has_role(session_user, alvo.oid, 'SET'),
                      EXISTS (SELECT 1 FROM outbound WHERE origem=alvo.oid), session_user
               FROM roles_alvo alvo ORDER BY alvo.rolname""",
            [owner, runtime],
        )
        rows = cursor.fetchall()
        cursor.execute(
            """WITH esperadas(oid) AS (VALUES
                 (to_regprocedure('public.faturamento_receber_evento(text,text,text,text,text,text,boolean,jsonb,text,timestamptz)')),
                 (to_regprocedure('public.faturamento_rotear_evento(text,text)'))
               ), roles_faturamento AS (
                 SELECT oid,rolname FROM pg_roles WHERE rolname IN (%s,%s)
               ), acl_relacoes AS (
                 SELECT c.relname,c.relkind,r.rolname,a.privilege_type,a.is_grantable
                 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
                 JOIN roles_faturamento r ON r.oid=a.grantee
                 WHERE c.relnamespace='public'::regnamespace
               ), acl_schema AS (
                 SELECT r.rolname,a.privilege_type,a.is_grantable
                 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a
                 JOIN roles_faturamento r ON r.oid=a.grantee WHERE n.nspname='public'
               ), acl_colunas AS (
                 SELECT 1
                 FROM pg_attribute att JOIN pg_class c ON c.oid=att.attrelid
                 CROSS JOIN LATERAL aclexplode(att.attacl) a
                 JOIN roles_faturamento r ON r.oid=a.grantee
                 WHERE c.relnamespace='public'::regnamespace
               ) SELECT
                 (SELECT count(*) FROM esperadas JOIN pg_proc p ON p.oid=esperadas.oid) = 2
                 AND (SELECT count(*) FROM pg_proc
                      WHERE pronamespace='public'::regnamespace
                        AND proname IN ('faturamento_receber_evento','faturamento_rotear_evento')) = 2
                 AND NOT EXISTS (
                   SELECT 1 FROM esperadas e
                   LEFT JOIN pg_proc p ON p.oid=e.oid LEFT JOIN pg_roles r ON r.oid=p.proowner
                   WHERE p.oid IS NULL OR NOT p.prosecdef OR r.rolname<>%s
                     OR has_function_privilege('public', p.oid, 'EXECUTE')
                     OR NOT has_function_privilege(%s, p.oid, 'EXECUTE')
                     OR COALESCE(cardinality(p.proconfig),0)<>1
                     OR replace(p.proconfig[1],' ','')<>'search_path=pg_catalog,pg_temp'
                 )
                 AND
                 NOT EXISTS (SELECT 1 FROM acl_relacoes WHERE rolname=%s)
                 AND (SELECT count(*) FROM acl_relacoes WHERE rolname=%s) = 6
                 AND NOT EXISTS (
                   SELECT 1 FROM acl_relacoes WHERE rolname=%s AND NOT (
                     (relname='evento_cobranca' AND relkind='r' AND privilege_type IN ('SELECT','INSERT','UPDATE'))
                     OR (relname='assinatura_gateway' AND relkind='r' AND privilege_type='SELECT')
                     OR (relname='evento_cobranca_id_seq' AND relkind='S' AND privilege_type IN ('SELECT','USAGE'))
                   ) OR is_grantable
                 )
                 AND (SELECT count(*) FROM acl_schema) = 2
                 AND NOT EXISTS (SELECT 1 FROM acl_schema WHERE privilege_type<>'USAGE' OR is_grantable)
                 AND NOT EXISTS (SELECT 1 FROM acl_colunas),
                 (SELECT count(*) FROM pg_policy
                   WHERE polrelid=to_regclass('public.evento_cobranca')
                     AND polname LIKE 'evento_interface_definidor_%%') = 3
                 AND NOT EXISTS (
                   SELECT 1 FROM pg_policy p
                   WHERE p.polrelid=to_regclass('public.evento_cobranca')
                     AND ((p.polname='evento_interface_definidor_select'
                           AND (NOT p.polpermissive OR p.polcmd<>'r' OR p.polroles<>ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]
                                OR pg_get_expr(p.polqual,p.polrelid) IS DISTINCT FROM 'true' OR p.polwithcheck IS NOT NULL))
                       OR (p.polname='evento_interface_definidor_insert'
                           AND (NOT p.polpermissive OR p.polcmd<>'a' OR p.polroles<>ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]
                                OR p.polqual IS NOT NULL OR pg_get_expr(p.polwithcheck,p.polrelid) IS DISTINCT FROM 'true'))
                       OR (p.polname='evento_interface_definidor_update'
                           AND (NOT p.polpermissive OR p.polcmd<>'w' OR p.polroles<>ARRAY[(SELECT oid FROM pg_roles WHERE rolname=%s)]
                                OR pg_get_expr(p.polqual,p.polrelid) IS DISTINCT FROM 'true'
                                OR pg_get_expr(p.polwithcheck,p.polrelid) IS DISTINCT FROM 'true'))
                       OR p.polname NOT IN ('evento_tenant','evento_interface_definidor_select',
                                            'evento_interface_definidor_insert','evento_interface_definidor_update'))
                 ),
                 (SELECT count(*) FROM esperadas JOIN pg_proc p ON p.oid=esperadas.oid)""",
            [owner, runtime, owner, runtime, runtime, owner, owner, owner, owner, owner],
        )
        catalogo = cursor.fetchone()

    if len(rows) != 2 or any(row[1:8] != (False, False, False, False, False, False, False) or row[9] for row in rows):
        return _erro(
            "Uma role PostgreSQL de faturamento está ausente ou possui atributos/memberships inseguros.",
            "faturamento.E003",
            (
                "Pré-provisione owner e runtime como NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE "
                "NOCREATEDB NOINHERIT NOREPLICATION, sem roles concedidas a elas."
            ),
        )
    membership = {row[0]: row[8] for row in rows}
    usuario = rows[0][10]
    membership_valida = {
        "web": not membership[owner] and not membership[runtime],
        "ingress": not membership[owner] and membership[runtime],
        "migration": membership[owner],
    }[modo]
    if usuario in {owner, runtime} or not membership_valida:
        return _erro(
            f"A credencial do modo {modo} não respeita as memberships de faturamento.",
            "faturamento.E004",
            "Web não recebe roles; ingress recebe runtime sem owner; migration inclui owner com SET OPTION.",
        )
    if (catalogo[2] == 0 and modo != "migration") or (catalogo[2] != 0 and catalogo[:2] != (True, True)):
        return _erro(
            "Owner ou policies das funções de faturamento divergem do contrato.",
            "faturamento.E006",
            "Reaplique a migration com a credencial DDL dedicada e não altere ownership/policies manualmente.",
        )
    return []
