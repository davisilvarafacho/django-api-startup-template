# Organização da suíte de testes

## Objetivo

Reorganizar a suíte sem reescrever o comportamento dos testes. A mudança deve
deixar explícitos o proprietário de cada teste, os limites da coleta do pytest e
as dependências que pertencem exclusivamente à infraestrutura de teste.

## Princípios

- Testes de um app continuam junto do app.
- Pastas representam a interface ou feature exercitada pelo teste.
- Markers representam custo ou dependência de execução; não substituem a
  organização por responsabilidade.
- Verificações que percorrem o repositório pertencem a `tests/architecture/`,
  não a um app específico.
- Código auxiliar usado somente por testes pertence a `tests/support/`.
- A reorganização deve preservar node IDs conceitualmente equivalentes e não
  alterar regras de negócio.

## Fronteira de coleta

O pytest terá `testpaths` explícito para `api/tests`, `apps`,
`internal_frameworks`, `utils` e `tests`. `.examples` continuará fora da suíte
principal tanto na coleta quanto em qualquer varredura arquitetural.

A varredura de mutações de autorização deixará
`internal_frameworks/permission_cache/tests/test_mutations.py`. Ela será movida
para `tests/architecture/test_authorization_writes.py` e percorrerá somente as
raízes de produção declaradas pelo projeto. Os testes funcionais das mutações
permanecerão no framework.

## Propriedade dos testes

Testes de `api.logging_config` e de configuração de e-mail sairão de
`apps/api/core/tests` e passarão para `api/tests`. Testes de management commands
do core serão agrupados em `apps/api/core/tests/management_commands/`.

Os hotspots ganharão somente os agrupamentos que já estão claros no domínio:

```text
apps/api/autenticacao/tests/
├── api_keys/
├── login/
├── mfa/
├── passwords/
└── tokens/

internal_frameworks/permission_cache/tests/
├── resolvers/
└── signals/
```

Arquivos transversais que não ganham localidade com uma subpasta permanecerão
na raiz do pacote de testes correspondente.

## Factory de usuário

`apps/usuarios/factories.py` não é funcionalidade de produção. O projeto
removerá `factory-boy` e substituirá `UsuarioFactory` por uma função pequena e
determinística em `tests/support/usuarios.py`.

A função manterá o comportamento necessário à suíte:

- criar e persistir `Usuario`;
- produzir e-mail único quando o chamador não informar um;
- aceitar sobrescrita de campos por keyword arguments;
- aplicar hash à senha sem acoplar testes alheios à política de senha.

`tests/README.md` documentará que `tests/support` contém apenas infraestrutura
de teste e não constitui interface de produção. Referências ativas a
`factory_boy` na documentação serão atualizadas.

## Fixtures e markers

A fixture de Redis será movida do `conftest.py` raiz para o pacote de testes de
`permission_cache`, seu único consumidor. Fixtures realmente transversais
permanecerão na raiz; seu escopo não será alterado sem evidência de que isso
preserva o isolamento atual.

Serão registrados os markers:

- `architecture`: verificações estáticas que percorrem código do repositório;
- `integration`: testes que dependem de infraestrutura externa ou coordenação
  entre processos;
- `redis`: subconjunto de integração que requer Redis real.

O `Makefile` oferecerá comandos para a suíte completa, para o subconjunto sem
integrações e para integrações.

## Compatibilidade e validação

A migração será feita apenas com movimentos e ajustes de imports, salvo pela
substituição autocontida da factory. Cada grupo será coletado e executado após
o movimento. Ao final serão executados:

```bash
uv run ruff check .
uv run --group test pytest --collect-only -q --nomigrations
make test
make docs
```

As alterações locais preexistentes serão preservadas e não serão incluídas
nos commits da reorganização, exceto por hunks diretamente necessários em
arquivos compartilhados como `pyproject.toml` e `uv.lock`.
