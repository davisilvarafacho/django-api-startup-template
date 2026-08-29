"""Definições reproduzíveis e consultas do catálogo operacional de planos."""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import connection, transaction
from django.utils import timezone

from apps.assinaturas.features import CATALOGO_RECURSOS, ValoresRecursos
from apps.assinaturas.models import Periodicidade, Plano, PrecoPlano, VersaoPlano

MOEDA = re.compile(r"^[A-Z]{3}$")
MAIOR_BIGINT = 2**63 - 1
MAIOR_SMALLINT = 2**15 - 1


@dataclass(frozen=True)
class DefinicaoPrecoPlano:
    periodicidade: Periodicidade
    moeda: str
    valor_base_centavos: int
    valor_seat_centavos: int
    is_active: bool = True

    def __post_init__(self):
        if not isinstance(self.periodicidade, Periodicidade):
            raise ValueError("Periodicidade de preço deve ser uma Periodicidade concreta.")
        if type(self.moeda) is not str or not MOEDA.fullmatch(self.moeda):
            raise ValueError("Moeda deve ter três letras maiúsculas.")
        centavos = (self.valor_base_centavos, self.valor_seat_centavos)
        if any(type(valor) is not int or not 0 <= valor <= MAIOR_BIGINT for valor in centavos):
            raise ValueError("Valores em centavos devem ser inteiros não negativos no intervalo de bigint.")
        if type(self.is_active) is not bool:
            raise ValueError("is_active do preço deve ser booleano.")


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
        if any(type(valor) is not int or not 0 <= valor <= MAIOR_SMALLINT for valor in inteiros) or self.numero == 0:
            raise ValueError("Número e limites da versão devem ser inteiros no intervalo de smallint, com número maior que zero.")
        if self.limite_seats_trial is not None and (type(self.limite_seats_trial) is not int or not 0 <= self.limite_seats_trial <= MAIOR_SMALLINT):
            raise ValueError("Limite de seats do trial deve ser nulo ou inteiro no intervalo de smallint.")
        if any(type(valor) is not bool for valor in (self.atual, self.expansao_automatica_seats, self.is_active)):
            raise ValueError("Booleanos da versão devem ser valores bool concretos.")
        if not isinstance(self.recursos, ValoresRecursos):
            raise ValueError("Recursos da versão devem ser ValoresRecursos.")
        if type(self.precos) is not tuple or not self.precos or not all(isinstance(preco, DefinicaoPrecoPlano) for preco in self.precos):
            raise ValueError("Preços da versão devem ser uma tupla de DefinicaoPrecoPlano não vazia.")
        if self.atual and (not self.is_active or not any(preco.is_active for preco in self.precos)):
            raise ValueError("Versão atual deve estar ativa e possuir ao menos um preço ativo.")


@dataclass(frozen=True)
class DefinicaoPlano:
    codigo: str
    nome: str
    descricao: str
    visivel: bool
    versoes: tuple[DefinicaoVersaoPlano, ...]
    is_active: bool = True

    def __post_init__(self):
        if type(self.codigo) is not str or not self.codigo or len(self.codigo) > 60:
            raise ValueError("O código do plano deve ser texto não vazio com até 60 caracteres.")
        if type(self.nome) is not str or not self.nome or len(self.nome) > 150:
            raise ValueError("O nome do plano deve ser texto não vazio com até 150 caracteres.")
        if type(self.descricao) is not str:
            raise ValueError("A descrição do plano deve ser texto.")
        if any(type(valor) is not bool for valor in (self.visivel, self.is_active)):
            raise ValueError("Booleanos do plano devem ser valores bool concretos.")
        if type(self.versoes) is not tuple or not self.versoes or not all(isinstance(versao, DefinicaoVersaoPlano) for versao in self.versoes):
            raise ValueError("Versões do plano devem ser uma tupla de DefinicaoVersaoPlano não vazia.")


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
        if aplicar:
            _adquirir_lock_catalogo()
            _bloquear_mutacoes_precos()
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


def _adquirir_lock_catalogo() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", ["apps.assinaturas.sync_plans"])


def _bloquear_mutacoes_precos() -> None:
    with connection.cursor() as cursor:
        cursor.execute("LOCK TABLE preco_plano IN EXCLUSIVE MODE")


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
    versao = VersaoPlano.objects.select_for_update().filter(plano=plano, numero=definicao.numero).first()
    nova_versao = versao is None
    if versao is None:
        if VersaoPlano.all_objects.filter(plano=plano, numero=definicao.numero, is_deleted=True).exists():
            raise ErroCatalogoPlanos(f"{plano.codigo} v{definicao.numero} está excluída e não pode ser selecionada pelo catálogo.")
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
            publicada_em=None,
            is_active=definicao.is_active,
        )
    else:
        if versao.publicada_em is None:
            raise ErroCatalogoPlanos(f"{plano.codigo} v{definicao.numero} existe, mas não está publicada; conclua ou remova o draft antes do sync.")
        _recusar_mutacao_versao(versao, definicao)
        _comparar_precos_publicados(versao, definicao, aplicar=aplicar, acoes=acoes)
        if versao.is_active != definicao.is_active:
            acoes.append(f"{'atualizaria' if not aplicar else 'atualizou'} atividade de '{plano.codigo}' v{definicao.numero}")
            if aplicar:
                versao.is_active = definicao.is_active
                versao.save(update_fields=["is_active"])

    if nova_versao:
        for definicao_preco in definicao.precos:
            _criar_preco(versao, definicao_preco, aplicar=aplicar, acoes=acoes, verbo=verbo)

    if nova_versao and aplicar:
        versao.publicada_em = timezone.now()
        versao.save(update_fields=["publicada_em"])


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


def _comparar_precos_publicados(
    versao: VersaoPlano,
    definicao: DefinicaoVersaoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
) -> None:
    precos_atuais = {
        (Periodicidade(preco.periodicidade), preco.moeda): preco for preco in PrecoPlano.objects.select_for_update().filter(versao_plano=versao)
    }
    precos_esperados = {(preco.periodicidade, preco.moeda): preco for preco in definicao.precos}
    if set(precos_atuais) != set(precos_esperados):
        raise ErroCatalogoPlanos(
            f"O conjunto de preços de {versao.plano.codigo} v{versao.numero} diverge do catálogo; declare o próximo número de versão."
        )

    for chave, definicao_preco in precos_esperados.items():
        preco = precos_atuais[chave]
        termos_atuais = (preco.valor_base_centavos, preco.valor_seat_centavos)
        termos_esperados = (definicao_preco.valor_base_centavos, definicao_preco.valor_seat_centavos)
        descricao = f"'{versao.plano.codigo}' v{versao.numero} {definicao_preco.periodicidade.label}/{definicao_preco.moeda}"
        if termos_atuais != termos_esperados:
            raise ErroCatalogoPlanos(f"Preço de {descricao} diverge do catálogo; declare o próximo número de versão.")
        if preco.is_active != definicao_preco.is_active:
            acoes.append(f"{'atualizaria' if not aplicar else 'atualizou'} atividade do preço {descricao}")
            if aplicar:
                preco.is_active = definicao_preco.is_active
                preco.save(update_fields=["is_active"])


def _criar_preco(
    versao: VersaoPlano,
    definicao: DefinicaoPrecoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
    verbo: str,
) -> None:
    descricao = f"'{versao.plano.codigo}' v{versao.numero} {definicao.periodicidade.label}/{definicao.moeda}"
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


def _sincronizar_versao_atual(
    plano: Plano,
    definicao: DefinicaoPlano,
    *,
    aplicar: bool,
    acoes: list[str],
) -> None:
    numero_atual = next(versao.numero for versao in definicao.versoes if versao.atual)
    atual_no_banco = VersaoPlano.objects.select_for_update().filter(plano=plano, atual=True).first()
    if atual_no_banco is not None and atual_no_banco.numero == numero_atual:
        return
    acoes.append(f"{'marcaria' if not aplicar else 'marcou'} '{plano.codigo}' v{numero_atual} como atual")
    if aplicar:
        alvo = VersaoPlano.objects.select_for_update().filter(plano=plano, numero=numero_atual, publicada_em__isnull=False, is_active=True).first()
        if alvo is None:
            existente = VersaoPlano.all_objects.filter(plano=plano, numero=numero_atual).first()
            if existente is not None and existente.is_deleted:
                raise ErroCatalogoPlanos(f"{plano.codigo} v{numero_atual} está excluída e não pode ser marcada como atual.")
            if existente is not None and existente.publicada_em is None:
                raise ErroCatalogoPlanos(f"{plano.codigo} v{numero_atual} não está publicada e não pode ser marcada como atual.")
            raise ErroCatalogoPlanos(f"{plano.codigo} v{numero_atual} não está ativa e contratável.")
        if not PrecoPlano.ativos.filter(versao_plano=alvo).exists():
            raise ErroCatalogoPlanos(f"{plano.codigo} v{numero_atual} não possui preço ativo e contratável.")
        if atual_no_banco is not None:
            atual_no_banco.atual = False
            atual_no_banco.save(update_fields=["atual"])
        alvo.atual = True
        alvo.save(update_fields=["atual"])
