"""Casos de uso que criam organizações globais."""

from apps.organizacoes.models import Organizacao


class Organizacoes:
    """Criação de organizações sem acoplar o model ao fluxo de onboarding."""

    @classmethod
    def criar(cls, *, nome: str, slug: str, email_faturamento: str | None = None) -> Organizacao:
        return Organizacao.objects.create(nome=nome, slug=slug, email_faturamento=email_faturamento)
