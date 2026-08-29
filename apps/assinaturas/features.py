"""Recursos de plano tipados, validados e serializáveis em JSON."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from apps.organizacoes.models import Papel

CHAVE_RECURSO = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class RecursoPlano[T]:
    """Declara um recurso e converte seus valores entre Python e JSON."""

    chave: str
    titulo: str
    descricao: str
    tipo: type[T]
    padrao: T
    unidade: str | None
    exemplo: T

    def validar(self, valor: object, *, origem: str = "o valor") -> T:
        """Valida sem coerção implícita e devolve o valor tipado."""
        if self.tipo is int:
            if type(valor) is not int or valor < 0:
                raise ValueError(f"{origem} deve ser inteiro.")
            return cast(T, valor)

        if self.tipo is bool:
            if type(valor) is not bool:
                raise ValueError(f"{origem} deve ser booleano.")
            return cast(T, valor)

        if self.tipo is str:
            if type(valor) is not str:
                raise ValueError(f"{origem} deve ser texto.")
            return cast(T, valor)

        if self.tipo is frozenset:
            if not isinstance(valor, (list, tuple, set, frozenset)):
                raise ValueError(f"{origem} deve ser uma lista.")

            tipo_item = self._tipo_item_colecao()
            try:
                itens = (tipo_item(item) for item in valor) if tipo_item is not None else iter(valor)
                return cast(T, frozenset(itens))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{origem} contém item inválido.") from exc

        if not isinstance(valor, self.tipo):
            raise ValueError(f"{origem} deve ser {self.tipo.__name__}.")
        return valor

    def para_json(self, valor: object) -> object:
        """Produz somente primitivas JSON e ordena coleções."""
        validado = self.validar(valor)
        if isinstance(validado, frozenset):
            return sorted(int(item) if isinstance(item, int) else item for item in validado)
        return validado

    def json_schema(self) -> dict[str, object]:
        """Gera o fragmento JSON Schema determinístico deste recurso."""
        schema: dict[str, object] = {
            "title": self.titulo,
            "description": self.descricao,
        }
        if self.tipo is int:
            schema.update({"type": "integer", "minimum": 0})
        elif self.tipo is bool:
            schema["type"] = "boolean"
        elif self.tipo is str:
            schema["type"] = "string"
        elif self.tipo is frozenset:
            item_schema: dict[str, object] = {}
            tipo_item = self._tipo_item_colecao()
            if tipo_item is not None and issubclass(tipo_item, int):
                item_schema["type"] = "integer"
                valores = getattr(tipo_item, "values", None)
                if valores is not None:
                    item_schema["enum"] = [int(valor) for valor in valores]
            else:
                item_schema["type"] = "string"
            schema.update({"type": "array", "items": item_schema})
        else:
            raise ValueError(f"tipo de recurso sem representação JSON Schema: {self.tipo.__name__}")

        schema["default"] = self.para_json(self.padrao)
        schema["examples"] = [self.para_json(self.exemplo)]
        if self.unidade is not None:
            schema["x-unit"] = self.unidade
        return schema

    def erros_declaracao(self) -> tuple[str, ...]:
        """Lista inconsistências consumidas pelo Django system check."""
        erros: list[str] = []
        if not CHAVE_RECURSO.fullmatch(self.chave):
            erros.append("a chave deve usar snake_case minúsculo.")
        if not self.titulo.strip():
            erros.append("o título não pode ser vazio.")
        if not self.descricao.strip():
            erros.append("a descrição não pode ser vazia.")

        for origem, valor in (("o valor padrão", self.padrao), ("o exemplo", self.exemplo)):
            try:
                self.validar(valor, origem=origem)
            except ValueError as exc:
                erros.append(str(exc))
        return tuple(erros)

    def _tipo_item_colecao(self) -> type | None:
        for colecao in (self.exemplo, self.padrao):
            if isinstance(colecao, (list, tuple, set, frozenset)) and colecao:
                return type(next(iter(colecao)))
        return None


class CatalogoRecursos:
    """Registro imutável das chaves de recurso conhecidas pela aplicação."""

    def __init__(self, recursos: Iterable[RecursoPlano[object]]):
        por_chave: dict[str, RecursoPlano[object]] = {}
        for recurso in recursos:
            if recurso.chave in por_chave:
                raise ValueError(f"chave duplicada no catálogo de recursos: {recurso.chave}")
            por_chave[recurso.chave] = recurso
        self._por_chave = MappingProxyType(por_chave)

    def obter_recurso(self, chave: str) -> RecursoPlano[object]:
        try:
            return self._por_chave[chave]
        except KeyError as exc:
            raise ValueError(f"recurso desconhecido no catálogo: {chave}") from exc

    def validar_snapshot(self, valores: Mapping[str, object]) -> ValoresRecursos:
        return ValoresRecursos(self, valores)

    def json_schema(self) -> dict[str, object]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "properties": {chave: self._por_chave[chave].json_schema() for chave in sorted(self._por_chave)},
        }

    def exemplos(self) -> dict[str, object]:
        return {chave: self._por_chave[chave].para_json(self._por_chave[chave].exemplo) for chave in sorted(self._por_chave)}

    def erros_declaracoes(self) -> tuple[tuple[str, str], ...]:
        return tuple((recurso.chave, erro) for recurso in self._por_chave.values() for erro in recurso.erros_declaracao())

    @property
    def chaves(self) -> tuple[str, ...]:
        return tuple(sorted(self._por_chave))


class ValoresRecursos:
    """Snapshot validado com lookup por objetos ``RecursoPlano``."""

    def __init__(self, catalogo: CatalogoRecursos, valores: Mapping[str, object]):
        desconhecidas = sorted(set(valores) - set(catalogo.chaves))
        if desconhecidas:
            raise ValueError(f"recurso desconhecido no snapshot: {desconhecidas[0]}")

        validados: dict[str, object] = {}
        for chave, valor in valores.items():
            recurso = catalogo.obter_recurso(chave)
            try:
                validados[chave] = recurso.validar(valor)
            except ValueError as exc:
                raise ValueError(f"recurso '{chave}': {exc}") from exc
        self._catalogo = catalogo
        self._valores = MappingProxyType(validados)

    def obter[T](self, recurso: RecursoPlano[T]) -> T:
        declarado = self._catalogo.obter_recurso(recurso.chave)
        if declarado != recurso:
            raise ValueError(f"recurso incompatível com o catálogo: {recurso.chave}")
        return recurso.validar(self._valores.get(recurso.chave, recurso.padrao))

    def materializar(self) -> dict[str, object]:
        resultado: dict[str, object] = {}
        for chave in self._catalogo.chaves:
            recurso = self._catalogo.obter_recurso(chave)
            resultado[chave] = recurso.para_json(self._valores.get(chave, recurso.padrao))
        return resultado


QUANTIDADE_PROJETOS = RecursoPlano[int](
    chave="quantidade_projetos",
    titulo="Quantidade de projetos",
    descricao="Quantidade máxima de projetos ativos.",
    tipo=int,
    padrao=1,
    unidade="projetos",
    exemplo=10,
)

PAPEIS_ISENTOS_SEAT = RecursoPlano[frozenset[Papel]](
    chave="papeis_isentos_seat",
    titulo="Papéis isentos de seat",
    descricao="Papéis que não consomem seat no contrato.",
    tipo=frozenset,
    padrao=frozenset(),
    unidade=None,
    exemplo=frozenset({Papel.MEMBRO}),
)

CATALOGO_RECURSOS = CatalogoRecursos((QUANTIDADE_PROJETOS, PAPEIS_ISENTOS_SEAT))
