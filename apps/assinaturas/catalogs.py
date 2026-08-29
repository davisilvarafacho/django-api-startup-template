"""Definições reproduzíveis e consultas do catálogo operacional de planos."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano

MOEDA = re.compile(r"^[A-Z]{3}$")


@dataclass(frozen=True)
class DefinicaoPrecoPlano:
    periodicidade: Periodicidade
    moeda: str
    valor_base_centavos: int
    valor_seat_centavos: int
    is_active: bool = True

    def __post_init__(self):
        if self.periodicidade not in Periodicidade.values:
            raise ValueError("Periodicidade de preço inválida.")
        if not MOEDA.fullmatch(self.moeda):
            raise ValueError("Moeda deve ter três letras maiúsculas.")
        if self.valor_base_centavos < 0 or self.valor_seat_centavos < 0:
            raise ValueError("Valores de preço não podem ser negativos.")


@dataclass(frozen=True)
class DefinicaoVersaoPlano:
    numero: int
    atual: bool
    seats_inclusos: int
    limite_seats_trial: int | None
    duracao_trial_dias: int
    carencia_pagamento_dias: int
    carencia_excesso_seats_dias: int
    expansao_automatica_seats: bool
    recursos: ValoresRecursos
    precos: tuple[DefinicaoPrecoPlano, ...]
    is_active: bool = True

    def __post_init__(self):
        inteiros = (
            self.numero,
            self.seats_inclusos,
            self.duracao_trial_dias,
            self.carencia_pagamento_dias,
            self.carencia_excesso_seats_dias,
        )
        if any(type(valor) is not int or valor < 0 for valor in inteiros) or self.numero == 0:
            raise ValueError("Número e limites da versão devem ser inteiros não negativos, com número maior que zero.")
        if self.limite_seats_trial is not None and (type(self.limite_seats_trial) is not int or self.limite_seats_trial < 0):
            raise ValueError("Limite de seats do trial deve ser nulo ou inteiro não negativo.")


@dataclass(frozen=True)
class DefinicaoPlano:
    codigo: str
    nome: str
    descricao: str
    visivel: bool
    versoes: tuple[DefinicaoVersaoPlano, ...]
    is_active: bool = True

    def __post_init__(self):
        if not self.codigo or not self.nome or not self.versoes:
            raise ValueError("Plano deve declarar código, nome e pelo menos uma versão.")


def _recursos(*, quantidade_projetos: int) -> ValoresRecursos:
    return ValoresRecursos(CATALOGO_RECURSOS, {"quantidade_projetos": quantidade_projetos})


PLANOS_BOOTSTRAP = (
    DefinicaoPlano(
        codigo="gratuito",
        nome="Gratuito",
        descricao="Plano gratuito para começar sem forma de pagamento.",
        visivel=True,
        versoes=(
            DefinicaoVersaoPlano(
                numero=1,
                atual=True,
                seats_inclusos=1,
                limite_seats_trial=None,
                duracao_trial_dias=0,
                carencia_pagamento_dias=0,
                carencia_excesso_seats_dias=7,
                expansao_automatica_seats=False,
                recursos=_recursos(quantidade_projetos=1),
                precos=(
                    DefinicaoPrecoPlano(
                        periodicidade=Periodicidade.MENSAL,
                        moeda="BRL",
                        valor_base_centavos=0,
                        valor_seat_centavos=0,
                    ),
                    DefinicaoPrecoPlano(
                        periodicidade=Periodicidade.ANUAL,
                        moeda="BRL",
                        valor_base_centavos=0,
                        valor_seat_centavos=0,
                    ),
                ),
            ),
        ),
    ),
    DefinicaoPlano(
        codigo="profissional",
        nome="Profissional",
        descricao="Plano pago de exemplo, provisionável no Stripe pelo módulo de faturamento.",
        visivel=True,
        versoes=(
            DefinicaoVersaoPlano(
                numero=1,
                atual=True,
                seats_inclusos=5,
                limite_seats_trial=10,
                duracao_trial_dias=14,
                carencia_pagamento_dias=7,
                carencia_excesso_seats_dias=7,
                expansao_automatica_seats=False,
                recursos=_recursos(quantidade_projetos=100),
                precos=(
                    DefinicaoPrecoPlano(
                        periodicidade=Periodicidade.MENSAL,
                        moeda="BRL",
                        valor_base_centavos=9900,
                        valor_seat_centavos=2000,
                    ),
                    DefinicaoPrecoPlano(
                        periodicidade=Periodicidade.ANUAL,
                        moeda="BRL",
                        valor_base_centavos=99000,
                        valor_seat_centavos=20000,
                    ),
                ),
            ),
        ),
    ),
)


class ErroCatalogoPlanos(ValueError):
    """Definição de código diverge do histórico publicado."""


class CatalogoPlanos:
    """Resolve o catálogo persistido usado pelos casos de uso comerciais."""

    @classmethod
    def obter_versao_inicial(
        cls,
        *,
        codigo: str,
        periodicidade: Periodicidade,
        moeda: str = "BRL",
    ) -> tuple[VersaoPlano, PrecoPlano]:
        plano = Plano.ativos.get(codigo=codigo)
        versao = VersaoPlano.ativos.get(plano=plano, atual=True, publicada_em__isnull=False)
        preco = PrecoPlano.ativos.get(
            versao_plano=versao,
            periodicidade=periodicidade,
            moeda=moeda,
        )
        return versao, preco


def erros_definicoes_planos(definicoes: tuple[DefinicaoPlano, ...]) -> tuple[str, ...]:
    erros: list[str] = []
    codigos: set[str] = set()
    for plano in definicoes:
        if plano.codigo in codigos:
            erros.append(f"código de plano duplicado: {plano.codigo}")
        codigos.add(plano.codigo)

        numeros: set[int] = set()
        atuais = 0
        for versao in plano.versoes:
            if versao.numero in numeros:
                erros.append(f"versão duplicada em {plano.codigo}: {versao.numero}")
            numeros.add(versao.numero)
            atuais += int(versao.atual)

            chaves_precos: set[tuple[Periodicidade, str]] = set()
            for preco in versao.precos:
                chave = (preco.periodicidade, preco.moeda)
                if chave in chaves_precos:
                    erros.append(f"preço duplicado em {plano.codigo} v{versao.numero}: {preco.periodicidade}/{preco.moeda}")
                chaves_precos.add(chave)
        if atuais != 1:
            erros.append(f"plano {plano.codigo} deve declarar exatamente uma versão atual")
    return tuple(erros)


def sincronizar_planos(definicoes: tuple[DefinicaoPlano, ...], *, aplicar: bool) -> tuple[str, ...]:
    """Compara ou aplica o bootstrap sem jamais editar termos publicados."""
    erros = erros_definicoes_planos(definicoes)
    if erros:
        raise ErroCatalogoPlanos("; ".join(erros))

    acoes: list[str] = []
    verbo = "criaria" if not aplicar else "criou"
    with transaction.atomic():
        for definicao_plano in definicoes:
            plano = Plano.all_objects.select_for_update().filter(codigo=definicao_plano.codigo, is_deleted=False).first()
            if plano is None:
                acoes.append(f"{verbo} plano '{definicao_plano.codigo}'")
                if not aplicar:
                    _descrever_ausencias(definicao_plano, acoes, verbo)
                    continue
                plano = Plano.objects.create(
                    codigo=definicao_plano.codigo,
                    nome=definicao_plano.nome,
                    descricao=definicao_plano.descricao,
                    visivel=definicao_plano.visivel,
                    is_active=definicao_plano.is_active,
                )
            else:
                _sincronizar_metadados_plano(plano, definicao_plano, aplicar=aplicar, acoes=acoes)

            for definicao_versao in definicao_plano.versoes:
                _sincronizar_versao(plano, definicao_versao, aplicar=aplicar, acoes=acoes, verbo=verbo)
            _sincronizar_versao_atual(plano, definicao_plano, aplicar=aplicar, acoes=acoes)
    return tuple(acoes)


def _descrever_ausencias(plano: DefinicaoPlano, acoes: list[str], verbo: str) -> None:
    for versao in plano.versoes:
        acoes.append(f"{verbo} versão '{plano.codigo}' v{versao.numero}")
        for preco in versao.precos:
            acoes.append(f"{verbo} preço '{plano.codigo}' v{versao.numero} {preco.periodicidade.label}/{preco.moeda}")


def _sincronizar_metadados_plano(
    plano: Plano,
    definicao: DefinicaoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
) -> None:
    campos = {
        "nome": definicao.nome,
        "descricao": definicao.descricao,
        "visivel": definicao.visivel,
        "is_active": definicao.is_active,
    }
    alterados = [campo for campo, valor in campos.items() if getattr(plano, campo) != valor]
    if not alterados:
        return
    acoes.append(f"{'atualizaria' if not aplicar else 'atualizou'} plano '{plano.codigo}'")
    if aplicar:
        for campo in alterados:
            setattr(plano, campo, campos[campo])
        plano.save(update_fields=alterados)


def _sincronizar_versao(
    plano: Plano,
    definicao: DefinicaoVersaoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
    verbo: str,
) -> None:
    versao = VersaoPlano.all_objects.select_for_update().filter(plano=plano, numero=definicao.numero).first()
    if versao is None:
        acoes.append(f"{verbo} versão '{plano.codigo}' v{definicao.numero}")
        if not aplicar:
            for preco in definicao.precos:
                acoes.append(f"{verbo} preço '{plano.codigo}' v{definicao.numero} {preco.periodicidade.label}/{preco.moeda}")
            return
        versao = VersaoPlano.objects.create(
            plano=plano,
            numero=definicao.numero,
            atual=False,
            seats_inclusos=definicao.seats_inclusos,
            limite_seats_trial=definicao.limite_seats_trial,
            duracao_trial_dias=definicao.duracao_trial_dias,
            carencia_pagamento_dias=definicao.carencia_pagamento_dias,
            carencia_excesso_seats_dias=definicao.carencia_excesso_seats_dias,
            expansao_automatica_seats=definicao.expansao_automatica_seats,
            recursos=definicao.recursos.materializar(),
            publicada_em=timezone.now(),
            is_active=definicao.is_active,
        )
    else:
        _recusar_mutacao_versao(versao, definicao)
        if versao.is_active != definicao.is_active:
            acoes.append(f"{'atualizaria' if not aplicar else 'atualizou'} atividade de '{plano.codigo}' v{definicao.numero}")
            if aplicar:
                versao.is_active = definicao.is_active
                versao.save(update_fields=["is_active"])

    for definicao_preco in definicao.precos:
        _sincronizar_preco(versao, definicao_preco, aplicar=aplicar, acoes=acoes, verbo=verbo)


def _recusar_mutacao_versao(versao: VersaoPlano, definicao: DefinicaoVersaoPlano) -> None:
    try:
        recursos_atuais = CATALOGO_RECURSOS.validar_snapshot(versao.recursos).materializar()
    except ValueError as exc:
        raise ErroCatalogoPlanos(f"{versao.plano.codigo} v{versao.numero} possui recursos inválidos; declare o próximo número de versão.") from exc
    esperado = {
        "seats_inclusos": definicao.seats_inclusos,
        "limite_seats_trial": definicao.limite_seats_trial,
        "duracao_trial_dias": definicao.duracao_trial_dias,
        "carencia_pagamento_dias": definicao.carencia_pagamento_dias,
        "carencia_excesso_seats_dias": definicao.carencia_excesso_seats_dias,
        "expansao_automatica_seats": definicao.expansao_automatica_seats,
        "recursos": definicao.recursos.materializar(),
    }
    atual = {campo: getattr(versao, campo) for campo in esperado}
    atual["recursos"] = recursos_atuais
    if atual != esperado:
        raise ErroCatalogoPlanos(f"{versao.plano.codigo} v{versao.numero} diverge do catálogo; declare o próximo número de versão.")


def _sincronizar_preco(
    versao: VersaoPlano,
    definicao: DefinicaoPrecoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
    verbo: str,
) -> None:
    preco = (
        PrecoPlano.all_objects.select_for_update()
        .filter(
            versao_plano=versao,
            periodicidade=definicao.periodicidade,
            moeda=definicao.moeda,
        )
        .first()
    )
    descricao = f"'{versao.plano.codigo}' v{versao.numero} {definicao.periodicidade.label}/{definicao.moeda}"
    if preco is None:
        acoes.append(f"{verbo} preço {descricao}")
        if aplicar:
            PrecoPlano.objects.create(
                versao_plano=versao,
                periodicidade=definicao.periodicidade,
                moeda=definicao.moeda,
                valor_base_centavos=definicao.valor_base_centavos,
                valor_seat_centavos=definicao.valor_seat_centavos,
                is_active=definicao.is_active,
            )
        return

    termos_atuais = (preco.valor_base_centavos, preco.valor_seat_centavos)
    termos_esperados = (definicao.valor_base_centavos, definicao.valor_seat_centavos)
    if termos_atuais != termos_esperados:
        raise ErroCatalogoPlanos(f"Preço de {descricao} diverge do catálogo; declare o próximo número de versão.")
    if preco.is_active != definicao.is_active:
        acoes.append(f"{'atualizaria' if not aplicar else 'atualizou'} atividade do preço {descricao}")
        if aplicar:
            preco.is_active = definicao.is_active
            preco.save(update_fields=["is_active"])


def _sincronizar_versao_atual(
    plano: Plano,
    definicao: DefinicaoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
) -> None:
    numero_atual = next(versao.numero for versao in definicao.versoes if versao.atual)
    atual_no_banco = VersaoPlano.all_objects.filter(plano=plano, atual=True, is_deleted=False).first()
    if atual_no_banco is not None and atual_no_banco.numero == numero_atual:
        return
    acoes.append(f"{'marcaria' if not aplicar else 'marcou'} '{plano.codigo}' v{numero_atual} como atual")
    if aplicar:
        VersaoPlano.all_objects.filter(plano=plano, atual=True).update(atual=False)
        alvo = VersaoPlano.all_objects.get(plano=plano, numero=numero_atual)
        alvo.atual = True
        alvo.save(update_fields=["atual"])
