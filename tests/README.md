# Infraestrutura compartilhada de testes

Este pacote contém somente suporte usado pela suíte e não faz parte da
interface de produção do template.

- `architecture/` verifica invariantes que atravessam mais de um app.
- `support/` oferece construtores e outros helpers compartilhados pelos testes.

Helpers deste pacote podem depender dos models do projeto, mas código de
produção não deve importar `tests`. Fixtures específicas de um app ou framework
devem permanecer no `conftest.py` mais próximo de seus consumidores.
