"""Abstrações tipadas sobre :mod:`contextvars`."""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from threading import Lock
from typing import Any, ClassVar, Literal, Self, overload
from weakref import WeakSet

_UNSET = object()


class ContextVariableNotSetError(LookupError):
    """Leitura obrigatória de uma variável contextual ausente."""


class ContextVariable[T]:
    """Uma variável contextual independente, com ausência semântica explícita."""

    _instances: ClassVar[WeakSet[Any]] = WeakSet()
    _instances_lock: ClassVar[Lock] = Lock()

    def __init__(self, name: str, default: T | None = None):
        self._name = name
        self._default = default
        self._variable: ContextVar[object] = ContextVar(name, default=_UNSET)
        with self._instances_lock:
            self._instances.add(self)

    @classmethod
    def from_var(cls, name: str, default: T | None = None) -> Self:
        """Cria uma variável contextual independente."""
        return cls(name, default)

    def set(self, value: T) -> Token[object]:
        """Define o valor no contexto de execução corrente."""
        return self._variable.set(value)

    @overload
    def get(self, *, raise_exception: Literal[True]) -> T: ...

    @overload
    def get(self, *, raise_exception: Literal[False] = False) -> T | None: ...

    def get(self, *, raise_exception: bool = False) -> T | None:
        """Obtém o valor atual ou o default configurado."""
        value = self._variable.get()
        if value is _UNSET:
            if raise_exception:
                raise ContextVariableNotSetError(f"A variável de contexto '{self._name}' não está definida.")
            return self._default
        return value  # type: ignore[return-value]

    def is_set(self) -> bool:
        """Informa se há valor definido no contexto corrente."""
        return self._variable.get() is not _UNSET

    def reset(self, token: Token[object]) -> None:
        """Restaura o estado associado a um token de :meth:`set`."""
        self._variable.reset(token)

    def clear(self) -> None:
        """Representa ausência no contexto corrente."""
        self._variable.set(_UNSET)

    @contextmanager
    def use(self, value: T) -> Generator[T, None, None]:
        """Define temporariamente um valor e restaura o estado anterior."""
        token = self.set(value)
        try:
            yield value
        finally:
            self.reset(token)

    @classmethod
    def clear_context(cls) -> None:
        """Limpa todas as variáveis vivas apenas no contexto corrente."""
        with cls._instances_lock:
            variables = tuple(cls._instances)
        for variable in variables:
            variable.clear()
