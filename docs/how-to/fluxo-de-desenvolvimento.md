# Fluxo de desenvolvimento

Estratégia de branches e ciclo de PR do projeto. Para preparar o ambiente veja
[Desenvolvimento local](desenvolvimento-local.md); para liberar uma versão, veja
[Publicar uma versão](publicar-versao.md).

## Estratégia: trunk-based

Uma única branch permanente: `main`. Todo trabalho acontece em branches curtas
(idealmente com vida menor que dois dias) que nascem de `main` e são apagadas no
merge. Sem branches `develop`/`release`/`hotfix` — o projeto faz deploy contínuo
e GitFlow só adicionaria cerimônia.

!!! note "Proteção de branch"
    Enquanto o projeto está no início, `main` aceita push direto. Quando
    estabilizar, ative a proteção (exigir PR + checks `commits`, `lint`, `test`)
    em **Settings → Branches**.

## Nomes de branch

O prefixo casa com o tipo do Conventional Commit:

| Prefixo | Uso |
| --- | --- |
| `feat/` | nova funcionalidade |
| `fix/` | correção de bug |
| `refactor/` | refatoração sem mudança de comportamento |
| `docs/` | documentação |
| `chore/` | manutenção, dependências, config |
| `test/` | apenas testes |

Exemplos: `feat/rls-org-scope`, `fix/permission-codename`.

## Ciclo de uma mudança

```bash
git switch main && git pull            # parta sempre do topo
git switch -c feat/<slug>              # branch curta

# ... commits seguindo Conventional Commits (o hook commit-msg valida) ...

git push -u origin feat/<slug>
gh pr create --fill                    # o PULL_REQUEST_TEMPLATE preenche a base
```

Antes de abrir o PR, garanta o que o `CONTRIBUTING.md` exige: `make lint`,
`make test` e `make docs` verdes. Os testes criam `base_test`, aplicam o grafo
real de migrations e destroem o banco ao final.

Execuções paralelas devem fornecer nomes distintos por `TEST_DATABASE_NAME`.
O valor muda somente o banco efêmero de testes; o banco de desenvolvimento
continua vindo de `DATABASE_NAME`.

## O pull request

1. **CI** roda três jobs — `commits` (valida Conventional Commits do range),
   `lint` (ruff, versões e docs) e `test` (gate de migrations, pytest com
   migrations reais e cobertura Codecov). Todos precisam passar.
2. **Review**: pelo menos uma aprovação.
3. **Squash merge**: cada PR vira um único commit na `main`. Mantém o histórico
   linear e o range de commits que o commitlint valida trivial. A branch é
   apagada no merge.

!!! tip "Configuração do repositório"
    Em **Settings → General → Pull Requests**, deixe habilitado apenas
    *Allow squash merging* e *Automatically delete head branches*.

## Dependabot

O Dependabot abre PRs de atualização de dependências. Seguem o mesmo fluxo:
CI verde e merge por squash.

## Release

Acúmulo de PRs na `main` até fechar uma versão. A partir daí, siga
[Publicar uma versão](publicar-versao.md): bump de versão SemVer, mover o
`CHANGELOG.md`, criar a tag `vX.Y.Z` e publicar a release.
