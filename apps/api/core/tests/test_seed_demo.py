"""Integration tests for the generic local demonstration seed."""
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError

import pytest

from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.usuarios.models import Usuario

pytestmark = pytest.mark.django_db


def executar_seed(**options):
    stdout = StringIO()
    call_command("seed_demo", stdout=stdout, **options)
    return stdout.getvalue()


def test_seed_demo_cria_grafo_generico():
    output = executar_seed()

    organizacao = Organizacao.objects.get(slug="demo")
    time = Time.objects.get(organizacao=organizacao, nome="Time Demo")
    usuario = Usuario.objects.get(email="demo@example.com")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)

    assert organizacao.nome == "Organização Demo"
    assert usuario.first_name == "Usuário"
    assert usuario.last_name == "Demo"
    assert usuario.check_password("demo123456")
    assert usuario.is_staff is False
    assert usuario.is_superuser is False
    assert vinculo.papel == Papel.PROPRIETARIO
    assert list(vinculo.times.all()) == [time]
    assert "demo@example.com" in output
    assert "demo123456" in output
    assert "X-Organization: demo" in output
    assert "POST /auth/login/" in output
    assert "Organização Demo: criado" in output
    assert "Time Demo: criado" in output
    assert "Usuário Demo: criado" in output
    assert "Vínculo Demo: criado" in output


def test_seed_demo_bloqueia_producao_antes_de_escrever(settings):
    settings.IN_PRODUCTION = True

    with pytest.raises(CommandError, match="--allow-production"):
        executar_seed()

    assert not Organizacao.objects.filter(slug="demo").exists()
    assert not Usuario.objects.filter(email="demo@example.com").exists()


def test_seed_demo_permite_execucao_deliberada_em_producao(settings):
    settings.IN_PRODUCTION = True

    executar_seed(allow_production=True)

    assert Organizacao.objects.filter(slug="demo").exists()
    assert Usuario.objects.filter(email="demo@example.com").exists()


def test_seed_demo_e_idempotente_e_preserva_edicoes_manuais():
    executar_seed()
    organizacao = Organizacao.objects.get(slug="demo")
    time_demo = Time.objects.get(organizacao=organizacao, nome="Time Demo")
    usuario = Usuario.objects.get(email="demo@example.com")
    vinculo = Vinculo.objects.get(organizacao=organizacao, usuario=usuario)
    time_extra = Time.objects.create(organizacao=organizacao, nome="Produto")

    organizacao.nome = "Nome editado"
    organizacao.save(update_fields=["nome"])
    usuario.first_name = "Nome"
    usuario.last_name = "Editado"
    usuario.is_staff = True
    usuario.is_superuser = True
    usuario.set_password("senha-editada")
    usuario.save(
        update_fields=["first_name", "last_name", "is_staff", "is_superuser", "password"]
    )
    vinculo.papel = Papel.MEMBRO
    vinculo.save(update_fields=["papel"])
    vinculo.times.set([time_extra])

    output = executar_seed()

    assert Organizacao.objects.filter(slug="demo").count() == 1
    assert Time.objects.filter(organizacao=organizacao, nome="Time Demo").count() == 1
    assert Usuario.objects.filter(email="demo@example.com").count() == 1
    assert Vinculo.objects.filter(organizacao=organizacao, usuario=usuario).count() == 1

    organizacao.refresh_from_db()
    usuario.refresh_from_db()
    vinculo.refresh_from_db()
    assert organizacao.nome == "Nome editado"
    assert usuario.get_full_name() == "Nome Editado"
    assert usuario.check_password("senha-editada")
    assert usuario.is_staff is True
    assert usuario.is_superuser is True
    assert vinculo.papel == Papel.MEMBRO
    assert set(vinculo.times.all()) == {time_demo, time_extra}
    assert "já existia" in output


def test_seed_demo_reverte_todo_o_grafo_quando_o_banco_falha(monkeypatch):
    def falhar_ao_criar_time(*args, **kwargs):
        raise RuntimeError("falha simulada")

    monkeypatch.setattr(Time.objects, "get_or_create", falhar_ao_criar_time)

    with pytest.raises(RuntimeError, match="falha simulada"):
        executar_seed()

    assert not Organizacao.objects.filter(slug="demo").exists()
    assert not Usuario.objects.filter(email="demo@example.com").exists()
