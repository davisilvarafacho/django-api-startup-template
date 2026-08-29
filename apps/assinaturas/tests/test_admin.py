from django.contrib import admin
from django.utils import timezone

from apps.assinaturas.models import Plano, PrecoPlano, VersaoPlano


def test_admin_nao_edita_versao_publicada_nem_seu_preco():
    plano = Plano(codigo="gratuito", nome="Gratuito")
    versao = VersaoPlano(plano=plano, numero=1, publicada_em=timezone.now())
    preco = PrecoPlano(versao_plano=versao)

    assert admin.site._registry[VersaoPlano].has_change_permission(None, versao) is False
    assert admin.site._registry[PrecoPlano].has_change_permission(None, preco) is False


def test_admin_nao_exclui_versao_nem_preco_do_historico():
    assert admin.site._registry[VersaoPlano].has_delete_permission(None) is False
    assert admin.site._registry[PrecoPlano].has_delete_permission(None) is False
