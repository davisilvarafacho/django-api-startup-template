import pytest

from apps.organizacoes.models import Organizacao


@pytest.mark.django_db
def test_objects_oculta_soft_deletados_e_all_objects_os_inclui():
    organizacao = Organizacao.objects.create(nome="Acme", slug="acme")
    organizacao.delete()

    assert not Organizacao.objects.filter(pk=organizacao.pk).exists()
    assert Organizacao.all_objects.filter(pk=organizacao.pk).exists()


@pytest.mark.django_db
def test_ativos_combina_registro_ativo_e_nao_excluido():
    ativa = Organizacao.objects.create(nome="Ativa", slug="ativa")
    inativa = Organizacao.objects.create(nome="Inativa", slug="inativa", is_active=False)
    excluida = Organizacao.objects.create(nome="Excluida", slug="excluida")
    excluida.delete()

    assert list(Organizacao.ativos.values_list("pk", flat=True)) == [ativa.pk]
    assert inativa.pk not in Organizacao.ativos.values_list("pk", flat=True)
