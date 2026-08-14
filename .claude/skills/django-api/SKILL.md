---
name: django-api
description: Use ao criar ou alterar models, serializers, views, filters ou migrations neste projeto — inclusive ao criar um app novo. Traz as regras específicas desta base que divergem do Django/DRF padrão.
---

# Django e DRF neste projeto

Esta base **diverge do Django padrão em vários pontos**. Conselho genérico de
Django frequentemente está errado aqui. Quando houver conflito, `docs/reference/convencoes.md`
é o documento normativo e vence.

## Antes de criar qualquer coisa

- Apps nascem por `python manage.py start_api_app <nome>`, **nunca** por
  `startapp`. O comando gera a estrutura correta e registra em `BUSINESS_APPS`.
- Cada módulo é **um arquivo** (`models.py`, `serializers.py`, `views.py`,
  `filters.py`, `handlers.py`, `docs.py`); `tests/` é a única exceção — sempre um
  pacote.
- Nomes de arquivo em inglês e no plural. Conceitos de domínio em português.
- Estrutura completa: `docs/reference/estrutura-de-diretorios.md`.

## Models

- Todo model herda de `Base` — que já traz organização (RLS), soft delete,
  timestamps, `created_by` e histórico de auditoria. Só use `BaseGlobal` para
  identidade lida antes de existir tenant, e justifique na docstring.
- Todo model implementa `__str__`.
- **Todo field declara `help_text` e `db_comment` com o mesmo valor.**
- `Meta` **sempre** declara `db_table`, `ordering = ("-id",)`, `verbose_name`,
  `verbose_name_plural` e `permissions`. O `ordering` é obrigatório aqui, ao
  contrário do conselho comum de evitá-lo.
- Choices no topo do `models.py`, preferindo `IntegerChoices`, com docstring
  indicando model e coluna. Exceção: códigos de erro da API são `TextChoices` e
  ficam em `<app>/errors.py`.
- Inteiro não negativo que o domínio limita a 32.000 → `PositiveSmallIntegerField`,
  sem validator ou constante extra para representar o limite.
- A ordem dos argumentos de cada field é fixa por tipo — ver convenções §2.2.
- Campos relacionais referenciam o model **por string** (`"organizacoes.Organizacao"`).
- Índices e constraints por organização: ver ADR 0007 e 0008.

## Serializers

- Leitura → `BaseModelSerpySerializer` (serpy). Escrita → `BaseModelSerializer` (DRF).
- Campos não expostos vão em `extra_internal_fields` no **model**; expostos e não
  editáveis, em `extra_read_only_fields`. O serializer consome ambos sozinho.
- Otimização de N+1 que depende dos campos do serializer vive nele, como
  `setup_eager_loading(queryset)` estático.

## Views

- Herança de `BaseModelViewSet`, que já entrega `grid`, `form`, `values`, `logs`,
  `bulk_create`, `bulk_update`, `clonar`, `invalidate_cache` e `ativar`/`inativar`.
  Não reimplemente nenhuma delas.
- `serializer_class` **ou** `serializer_classes = {"<action>": ...}` — nunca os dois.
- **Toda view define `filterset_class` explicitamente.**
- Recurso/coleção → `ModelViewSet`/`ViewSet`. Comando sem recurso central (login,
  reset de senha, desafio MFA) → `APIView`. Não crie ViewSet de uma action só.
- `select_related`/`prefetch_related` obrigatórios; `only()`/`values()` para
  limitar colunas.
- Toda rota exige `X-Organization` por padrão. Exceções são declaradas por
  registry (`PUBLIC_ROUTES` em `urls.py`, `TENANT_FREE_ROUTES` em
  `tenant_free_routes.py`) ou pelos decorators `@public` / `@no_tenancy` — nunca
  afrouxando a permission global.

## Testes

- pytest + pytest-django. Testes no pacote `tests/` do próprio app; quando cresce,
  divide em subpacotes por tema.
- Construtores compartilhados ficam em `tests/support/` (ex.: `criar_usuario`).
  **Não** há model-bakery nem factory-boy neste projeto.
- Fora do ciclo de request (task, management command, teste), envolva o código em
  `organizacao_atual(id)` — sem contexto, a query levanta `RLSContextRequiredError`.
- Celery roda eager em teste. Prefira testes DB-less quando o comportamento não
  depende do banco.

## Armadilhas específicas desta base

- **Não troque o `ENGINE` do banco**: `django_rls.backends.postgresql` é
  obrigatório. Com o engine padrão as policies de RLS são ignoradas em silêncio.
- Autenticação acontece no middleware, não no DRF. O DRF só reaproveita o
  resultado via `PassthroughAuthentication`.
- Leitura de ambiente sempre por `utils/env.py`, nunca `os.environ` cru.
- Não adicione app ou middleware condicional no `settings.py`: declare-o na função
  do ambiente em `api/configure_enviroment.py`.
- Actions fora dos verbos do Django usam o prefixo `can_` na permission e devem
  estar em `Meta.permissions`.
