# .skills

Skills de agente instaladas **por projeto** (não globalmente). Os arquivos reais moram aqui;
`.claude/skills/` e `.codex/skills/` contêm symlinks relativos apontando para cá, porque esses
são os únicos diretórios que o Claude Code e o Codex CLI descobrem automaticamente.

```
.skills/<pack>/<skill>/SKILL.md      <- arquivo real
.claude/skills/<skill> -> ../../.skills/<pack>/<skill>
.codex/skills/<skill>  -> ../../.skills/<pack>/<skill>
```

## Packs

| Pack | Origem | Commit | Skills |
|---|---|---|---|
| `superpowers/` | github.com/obra/superpowers | `44c9b2d` (2026-07-28) | 14 |
| `mattpocock/` | github.com/mattpocock/skills | `2ab9580` (2026-07-28) | 41 |
| `caveman/` | github.com/juliusbrussee/caveman | `0d95a81` (2026-07-03) | 7 |
| `ponytail/` | github.com/DietrichGebert/ponytail | `16f2980` (2026-07-15) | 6 |

Só a pasta `skills/` de cada repositório foi copiada (mais o `LICENSE`). Nenhum `.git`,
submódulo ou remote foi trazido — o conteúdo é versionado junto com este projeto.

No pack `mattpocock/` as categorias do repositório de origem (`engineering/`, `misc/`,
`productivity/`, `personal/`, `in-progress/`, `deprecated/`) foram achatadas, já que o
loader espera `<skills>/<nome>/SKILL.md` num nível só.

## Notas

- **Precedência:** skills de projeto vencem as globais de `~/.claude/skills` e `~/.codex/skills`
  quando o nome coincide. Como as 4 origens também estão instaladas globalmente na máquina,
  aqui elas ficam pinadas nos commits da tabela acima.
- **Hooks não vieram.** Alguns skills dependem de infra de nível de repositório que não é
  skill: o `session-start` do superpowers, o mode-tracker/statusline do ponytail e do caveman,
  e os subagentes `cavecrew-*`. Isso continua vindo da instalação global — este diretório
  cobre apenas os skills.
- **Atualizar um pack:** clonar o repo com `--depth 1`, substituir a pasta do pack por
  `<repo>/skills/`, e rodar de novo os symlinks para nomes novos.

## Regerar os symlinks

```sh
for pack in superpowers caveman ponytail mattpocock; do
  for dir in .skills/$pack/*/; do
    name=$(basename "$dir")
    ln -sfn "../../.skills/$pack/$name" ".claude/skills/$name"
    ln -sfn "../../.skills/$pack/$name" ".codex/skills/$name"
  done
done
```
