"""Predicados de autorização (django-rules) baseados no papel do vínculo.

Divisão de responsabilidades entre as três camadas de autorização:

- **Permissão do Django/guardian**: "pode executar a ação X?" (global ou por objeto).
- **Papel** (aqui): "qual o nível deste usuário **nesta** organização?".
- **RLS**: "quais linhas pertencem a esta organização?".

O papel precisa ser uma camada própria porque as permissions do Django são
globais — não há como expressar "administrador na Org A, membro na Org B" com
Groups nativos.
"""
import rules

from apps.organizacoes.models import Papel, Vinculo


def papel_minimo(papel):
    """Cria um predicado que exige `papel` ou superior na organização do objeto.

    O objeto avaliado precisa expor `organizacao_id` (todo modelo que herda de
    `Base` expõe).
    """

    @rules.predicate
    def _predicado(usuario, obj=None):
        if usuario is None or not usuario.is_authenticated:
            return False

        organizacao_id = getattr(obj, "organizacao_id", None)
        if organizacao_id is None:
            return False

        return Vinculo.objects.filter(
            usuario=usuario,
            organizacao_id=organizacao_id,
            is_active=True,
            papel__gte=papel,
        ).exists()

    return _predicado


e_visualizador = papel_minimo(Papel.VISUALIZADOR)
e_membro = papel_minimo(Papel.MEMBRO)
e_gestor = papel_minimo(Papel.GESTOR)
e_administrador = papel_minimo(Papel.ADMINISTRADOR)
e_proprietario = papel_minimo(Papel.PROPRIETARIO)
