import asyncio
import gc
import threading
import weakref

import pytest

from internal_frameworks.context import ContextVariable, ContextVariableNotSetError


def test_get_obrigatorio_falha_quando_variavel_esta_ausente():
    value = ContextVariable[str].from_var("value", default="fallback")

    with pytest.raises(ContextVariableNotSetError, match="value"):
        value.get(raise_exception=True)


def test_set_none_e_diferente_de_ausencia():
    value = ContextVariable[str | None].from_var("value")
    value.set(None)

    assert value.is_set() is True
    assert value.get(raise_exception=True) is None


def test_use_restaura_valor_anterior_apos_excecao():
    value = ContextVariable[str].from_var("value")
    value.set("antes")

    with pytest.raises(RuntimeError):
        _explode_during_context(value)

    assert value.get() == "antes"


def _explode_during_context(value):
    with value.use("durante"):
        assert value.get() == "durante"
        raise RuntimeError


def test_clear_context_limpa_todas_as_variaveis_no_contexto_atual():
    first = ContextVariable[str].from_var("first")
    second = ContextVariable[int].from_var("second", default=0)
    first.set("value")
    second.set(1)

    ContextVariable.clear_context()

    assert first.is_set() is False
    assert first.get() is None
    assert second.is_set() is False
    assert second.get() == 0


def test_instancias_com_mesmo_nome_sao_independentes():
    first = ContextVariable[str].from_var("same-name")
    second = ContextVariable[str].from_var("same-name")
    first.set("first")

    assert second.get() is None


def test_reset_com_token_de_outra_variavel_mantem_erro_nativo():
    first = ContextVariable[str].from_var("first")
    second = ContextVariable[str].from_var("second")
    token = first.set("first")

    with pytest.raises(ValueError, match="different ContextVar"):
        second.reset(token)


def test_instancias_sem_referencias_saem_do_registro_fraco():
    value = ContextVariable[str].from_var("temporary")
    reference = weakref.ref(value)

    del value
    gc.collect()

    assert reference() is None


def test_contexto_e_isolado_entre_threads():
    value = ContextVariable[str].from_var("thread")
    value.set("main")
    seen = []

    def read_context():
        seen.append(value.get())
        value.set("thread")

    thread = threading.Thread(target=read_context)
    thread.start()
    thread.join()

    assert seen == [None]
    assert value.get() == "main"


def test_contexto_e_isolado_entre_tasks_assincronas():
    value = ContextVariable[str].from_var("task")

    async def read_context(name):
        value.set(name)
        await asyncio.sleep(0)
        return value.get()

    async def read_all():
        return await asyncio.gather(read_context("one"), read_context("two"))

    assert asyncio.run(read_all()) == ["one", "two"]
