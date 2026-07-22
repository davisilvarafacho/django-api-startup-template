# Processo de lançamento de versão

Guia definitivo para versionar e lançar este projeto. O versionamento segue
**SemVer** e é automatizado pelo **commitizen** a partir das mensagens de commit
(**Conventional Commits**). **Não edite a versão à mão** — o `cz bump` cuida disso.

---

## 1. Onde a versão vive

| Local | Conteúdo | Como é atualizado |
|---|---|---|
| `pyproject.toml` → `[project] version` | Fonte da verdade (SemVer) | **Automático** (`cz`, provider `pep621`) |
| `api/settings.py` → `SPECTACULAR_SETTINGS["VERSION"]` | Versão exposta na doc da API (OpenAPI) | **Automático** (`cz`, `version_files`) |
| Git tag `vX.Y.Z` | Marca imutável do release | **Automático** (`cz bump`) |
| `CHANGELOG.md` | Histórico legível por humanos | **Automático** (`cz bump`: `## Unreleased` → `## vX.Y.Z`) |

> A configuração vive em `[tool.commitizen]` no `pyproject.toml`. Se um novo lugar
> passar a repetir a versão, adicione-o em `version_files` — **nunca** sincronize à mão.

---

## 2. Como a versão é decidida

O incremento é derivado dos commits desde a última tag:

| Commit | Incremento |
|---|---|
| `fix:` | **PATCH** (`0.1.0 → 0.1.1`) |
| `feat:` | **MINOR** (`0.1.0 → 0.2.0`) |
| `feat!:` / `fix!:` / rodapé `BREAKING CHANGE:` | **MAJOR** — mas veja a nota abaixo |
| `docs:`, `chore:`, `ci:`, `test:`, `refactor:`, `style:`, `perf:`, `build:` | Não altera a versão sozinho |

> **`major_version_zero = true`**: enquanto o projeto está em `0.x`, um breaking change
> vira **MINOR** (não estoura para `1.0.0`). Ao decidir que a API estabilizou, faça o
> primeiro `1.0.0` explicitamente (`cz bump 1.0.0`) e remova essa flag.

---

## 3. Pré-requisitos (antes de qualquer release)

- [ ] `pre-commit` instalado (`uv run pre-commit install`) — habilita os hooks
      `pre-commit` **e** `commit-msg` (validação de Conventional Commits).
- [ ] Você está na `main` atualizada (`git pull`) e a **working tree está limpa**.
- [ ] Toda a história desde a última tag usa **Conventional Commits**.
- [ ] Suíte verde, lint limpo e migrations conferidas:
  ```bash
  uv run pytest
  uv run ruff check .
  uv run python manage.py makemigrations --check --dry-run
  ```
- [ ] **CI verde** no commit que será lançado.

---

## 4. Passo a passo do release

```bash
# 1. Prever o que vai acontecer (não escreve nada):
uv run cz bump --dry-run --yes

# 2. Executar o bump de fato:
uv run cz bump
```

O `cz bump` faz, automaticamente, **tudo isto em um único passo**:

1. Calcula a nova versão a partir dos commits.
2. Atualiza `pyproject.toml` **e** `api/settings.py` (SPECTACULAR VERSION).
3. Move a seção `## Unreleased` do `CHANGELOG.md` para `## vX.Y.Z (data)`.
4. Cria o commit `bump: version X → Y`.
5. Cria a tag `vX.Y.Z`.

Depois:

```bash
# 3. Revisar o resultado antes de publicar:
git show           # confere o commit de bump (pyproject, settings, CHANGELOG)
git log --oneline -3

# 4. Publicar commit + tag:
git push --follow-tags origin main
```

5. **(Opcional)** Criar o release no GitHub a partir da tag `vX.Y.Z`, colando a
   seção correspondente do `CHANGELOG.md` como notas.
6. **(Opcional)** Disparar/validar o deploy do ambiente correspondente.

---

## 5. Casos especiais

```bash
# Forçar um incremento específico (ignora a inferência pelos commits):
uv run cz bump --increment PATCH        # ou MINOR / MAJOR

# Definir a versão manualmente (ex.: primeiro 1.0.0):
uv run cz bump 1.0.0

# Pré-release (release candidate):
uv run cz bump --prerelease rc          # ex.: 0.2.0rc0
```

### Escrever commits no padrão

```bash
uv run cz commit        # assistente interativo que monta a mensagem
uv run cz check --commit-msg-file <arquivo>   # valida uma mensagem
```

O commit de `bump:` também é válido perante o hook (o tipo `bump` faz parte do padrão).

---

## 6. Resumo: o que muda em um release

| Alterado automaticamente pelo `cz bump` | Feito por você |
|---|---|
| `pyproject.toml` (`version`) | Garantir pré-requisitos (testes/lint/CI) |
| `api/settings.py` (SPECTACULAR VERSION) | Revisar o commit de bump e o CHANGELOG |
| `CHANGELOG.md` | `git push --follow-tags` |
| Commit `bump: ...` + tag `vX.Y.Z` | Release no GitHub / deploy (opcional) |

---

## 7. Troubleshooting

- **`commit-msg` bloqueou o commit** → a mensagem não segue Conventional Commits.
  Use `uv run cz commit` para montá-la corretamente.
- **`cz bump` pergunta "Is this the first tag created?"** → acontece quando ainda não
  há tags. Responda conforme o caso ou rode com `--yes`.
- **"No commits found to bump"** → não há commits que alterem a versão (`feat`/`fix`/
  breaking) desde a última tag. Só há `docs`/`chore`/etc. — nada a lançar.
- **A versão saiu errada em algum arquivo** → confira o padrão em
  `[tool.commitizen] version_files` no `pyproject.toml`.
