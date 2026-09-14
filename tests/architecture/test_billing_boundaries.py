import ast
import os
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.architecture

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_nucleo_assinaturas_nao_importa_subapp_faturamento():
    violacoes = []
    raiz = REPOSITORY_ROOT / "apps" / "assinaturas"
    for caminho in raiz.rglob("*.py"):
        partes = caminho.relative_to(raiz).parts
        if "subapps" in partes or "tests" in partes:
            continue
        arvore = ast.parse(caminho.read_text(encoding="utf-8"))
        for no in ast.walk(arvore):
            modulos = []
            if isinstance(no, ast.Import):
                modulos = [alias.name for alias in no.names]
            elif isinstance(no, ast.ImportFrom) and no.module:
                modulos = [no.module]
            if any(modulo.startswith("apps.assinaturas.subapps") for modulo in modulos):
                violacoes.append(f"{caminho.relative_to(REPOSITORY_ROOT)}:{no.lineno}")

    assert violacoes == []


@pytest.mark.parametrize(
    ("compose_path", "web_service", "ingress_port"),
    [
        ("docker-compose.yml", "web", "80"),
        (".devcontainer/docker-compose.yml", "app", "8002"),
    ],
)
def test_compose_isola_http_e_workers_de_ingresso(compose_path, web_service, ingress_port):
    compose = yaml.safe_load((REPOSITORY_ROOT / compose_path).read_text(encoding="utf-8"))
    services = compose["services"]

    assert services[web_service]["environment"]["BILLING_DATABASE_MODE"] == "web"
    assert services[web_service]["environment"]["DATABASE_USER"] != services["db"]["environment"]["POSTGRES_USER"]
    credenciais_privilegiadas = {
        "BILLING_INGRESS_DATABASE_USER",
        "BILLING_INGRESS_DATABASE_PASSWORD",
        "BILLING_INGRESS_WORKER_DATABASE_USER",
        "BILLING_INGRESS_WORKER_DATABASE_PASSWORD",
        "BILLING_MIGRATION_DATABASE_USER",
        "BILLING_MIGRATION_DATABASE_PASSWORD",
    }
    if compose_path == ".devcontainer/docker-compose.yml":
        # Ausentes (em vez de vazias) para que `make migrate` possa aplicar os
        # defaults locais sem expor a credencial no processo web.
        assert credenciais_privilegiadas.isdisjoint(services[web_service]["environment"])
    else:
        assert all(services[web_service]["environment"][chave] == "" for chave in credenciais_privilegiadas)
    assert services["billing_ingress"]["environment"]["BILLING_DATABASE_MODE"] == "ingress"
    assert "BILLING_INGRESS_DATABASE_USER" in services["billing_ingress"]["environment"]["DATABASE_USER"]
    assert "BILLING_INGRESS_DATABASE_PASSWORD" in services["billing_ingress"]["environment"]["DATABASE_PASSWORD"]
    assert ingress_port in services["billing_ingress"]["expose"]
    assert "ports" not in services["billing_ingress"]

    worker_geral = services["celery_worker" if compose_path == "docker-compose.yml" else "worker"]
    worker_ingresso = services["billing_ingress_worker"]
    assert worker_geral["environment"]["BILLING_DATABASE_MODE"] == "web"
    assert "billing_ingress" not in str(worker_geral["command"])
    assert worker_ingresso["environment"]["BILLING_DATABASE_MODE"] == "ingress"
    assert "BILLING_INGRESS_WORKER_DATABASE_USER" in worker_ingresso["environment"]["DATABASE_USER"]
    assert "BILLING_INGRESS_WORKER_DATABASE_PASSWORD" in worker_ingresso["environment"]["DATABASE_PASSWORD"]
    assert "billing_ingress" in str(worker_ingresso["command"])

    if compose_path == ".devcontainer/docker-compose.yml":
        assert "gunicorn" in str(services["billing_ingress"]["command"])
        assert "runserver" not in str(services["billing_ingress"]["command"])
    else:
        for service_name in (web_service, "mcp", "celery_worker", "billing_ingress", "billing_ingress_worker", "celery_beat"):
            environment = services[service_name]["environment"]
            assert environment["POSTGRES_ADMIN_USER"] == ""
            assert environment["POSTGRES_ADMIN_PASSWORD"] == ""


def test_bootstrap_local_cria_login_web_sem_roles_financeiras():
    script = (REPOSITORY_ROOT / "docker" / "postgres" / "init-billing-roles.sh").read_text(encoding="utf-8")

    assert "BILLING_WEB_DATABASE_USER" in script
    assert "CREATE ROLE %I LOGIN" in script
    assert "GRANT billing_ingress_runtime" in script
    assert "GRANT billing_functions_owner" in script
    assert "GRANT billing_ingress_runtime TO %I" in script
    assert "GRANT billing_functions_owner TO %I" in script
    assert "GRANT SELECT (id, organizacao_id, assinatura_id, variante, identificador_externo, is_active, is_deleted)" in script
    assert "ON public.assinatura_gateway TO billing_ingress_runtime" in script
    assert "ON public.checkout_cobranca TO billing_ingress_runtime" in script
    assert "to_regclass('public.assinatura_gateway') IS NOT NULL" in script
    assert "to_regclass('public.checkout_cobranca') IS NOT NULL" in script
    assert "GRANT billing_ingress_runtime TO app_web" not in script
    assert "ALL TABLES IN SCHEMA public TO %I', :'ingress_user'" not in script
    assert "ALL TABLES IN SCHEMA public TO %I', :'ingress_worker_user'" in script


def test_script_bootstrap_transporta_segredos_por_ambiente_sem_expor_no_argv(tmp_path):
    captura_argumentos = tmp_path / "argumentos"
    captura_stdin = tmp_path / "stdin"
    psql_falso = tmp_path / "psql"
    psql_falso.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURA_ARGUMENTOS"\ncat > "$CAPTURA_STDIN"\n',
        encoding="utf-8",
    )
    psql_falso.chmod(0o755)
    segredos = {
        "BILLING_WEB_DATABASE_PASSWORD": "segredo-web-unico",
        "BILLING_INGRESS_DATABASE_PASSWORD": "segredo-http-unico",
        "BILLING_INGRESS_WORKER_DATABASE_PASSWORD": "segredo-worker-unico",
    }
    ambiente = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "CAPTURA_ARGUMENTOS": str(captura_argumentos),
        "CAPTURA_STDIN": str(captura_stdin),
        "POSTGRES_USER": "postgres",
        "POSTGRES_DB": "base",
        "BILLING_WEB_DATABASE_USER": "app_web",
        "BILLING_INGRESS_DATABASE_USER": "billing_http",
        "BILLING_INGRESS_WORKER_DATABASE_USER": "billing_worker",
        **segredos,
    }

    subprocess.run(
        ["sh", str(REPOSITORY_ROOT / "docker" / "postgres" / "init-billing-roles.sh")],
        check=True,
        env=ambiente,
    )

    argumentos = captura_argumentos.read_text(encoding="utf-8")
    entrada = captura_stdin.read_text(encoding="utf-8")
    assert all(segredo not in argumentos for segredo in segredos.values())
    assert all(segredo not in entrada for segredo in segredos.values())
    assert "--set=" not in argumentos
    assert "\\getenv web_password BILLING_WEB_DATABASE_PASSWORD" in entrada
    assert "\\getenv ingress_password BILLING_INGRESS_DATABASE_PASSWORD" in entrada
    assert "\\getenv ingress_worker_password BILLING_INGRESS_WORKER_DATABASE_PASSWORD" in entrada


def test_billing_bootstrap_reaplica_roles_no_volume_existente_sem_expor_senhas_na_cli():
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "billing-bootstrap" in makefile.splitlines()[0]
    assert "BILLING_COMPOSE_FILE := $(or $(BILLING_COMPOSE_FILE),docker-compose.yml)" in makefile
    alvo = makefile.split("billing-bootstrap:", 1)[1].split("\n\n", 1)[0]
    subir_db = 'docker compose -f "$(BILLING_COMPOSE_FILE)" up -d --wait db'
    executar_script = 'docker compose -f "$(BILLING_COMPOSE_FILE)" exec -T db sh /docker-entrypoint-initdb.d/10-billing-roles.sh'

    assert subir_db in alvo
    assert executar_script in alvo
    assert alvo.index(subir_db) < alvo.index(executar_script)
    assert "PASSWORD" not in alvo


def test_make_aplica_defaults_locais_mesmo_quando_credenciais_chegam_vazias():
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")

    assert "POSTGRES_ADMIN_USER := $(or $(POSTGRES_ADMIN_USER),postgres)" in makefile
    assert "POSTGRES_ADMIN_PASSWORD := $(or $(POSTGRES_ADMIN_PASSWORD),postgres)" in makefile
    assert "BILLING_MIGRATION_DATABASE_USER := $(or $(BILLING_MIGRATION_DATABASE_USER),$(POSTGRES_ADMIN_USER))" in makefile
    assert "BILLING_MIGRATION_DATABASE_PASSWORD := $(or $(BILLING_MIGRATION_DATABASE_PASSWORD),$(POSTGRES_ADMIN_PASSWORD))" in makefile
    assert makefile.index("BILLING_MIGRATION_DATABASE_USER :=") < makefile.index("billing-migrate: export")


@pytest.mark.parametrize("ambiente", ["production", "development"])
def test_nginx_envia_exclusivamente_webhook_ao_http_ingress(ambiente):
    configuracao = (REPOSITORY_ROOT / "docker" / "nginx" / "sites" / ambiente / "default.conf").read_text(encoding="utf-8")
    inicio = configuracao.index("location ^~ /faturamento/webhooks/")
    fim = configuracao.index("}", inicio)
    bloco = configuracao[inicio:fim]

    assert "proxy_pass http://billing_ingress_backend;" in bloco
    assert "proxy_pass http://api_backend;" not in bloco
    assert configuracao.index("location ^~ /faturamento/webhooks/") < configuracao.index("location / {")
