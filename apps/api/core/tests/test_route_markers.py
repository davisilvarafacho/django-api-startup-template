"""Testes dos marcadores de rota por decorator (não tocam o banco)."""

from apps.api.core.route_markers import (
    MARCADOR_PUBLICA,
    MARCADOR_SEM_TENANCY,
    no_tenancy,
    public,
    tem_marcador,
    view_do_path,
)


def test_public_marca_a_view():
    @public
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert not tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)


def test_no_tenancy_marca_a_view():
    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)
    assert not tem_marcador(Qualquer, MARCADOR_PUBLICA)


def test_decorators_sao_acumulaveis():
    @public
    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert tem_marcador(Qualquer, MARCADOR_SEM_TENANCY)


def test_marcador_vale_para_instancia():
    """O DRF entrega a instância da view para a permissão, não a classe."""

    @no_tenancy
    class Qualquer:
        pass

    assert tem_marcador(Qualquer(), MARCADOR_SEM_TENANCY)


def test_marcador_funciona_em_funcao():
    @public
    def uma_view(request):
        return None

    assert tem_marcador(uma_view, MARCADOR_PUBLICA)


def test_view_sem_decorator_nao_tem_marcador():
    class Qualquer:
        pass

    assert not tem_marcador(Qualquer, MARCADOR_PUBLICA)
    assert not tem_marcador(None, MARCADOR_PUBLICA)


def test_view_do_path_resolve_view_de_classe():
    """`/auth/login/` é uma APIView do DRF: deve devolver a classe, não o wrapper."""
    view = view_do_path("/auth/login/")

    assert view is not None
    assert isinstance(view, type)


def test_view_do_path_resolve_view_de_funcao():
    assert view_do_path("/health/") is not None


def test_view_do_path_retorna_none_para_rota_inexistente():
    assert view_do_path("/rota/que/nao/existe/") is None
