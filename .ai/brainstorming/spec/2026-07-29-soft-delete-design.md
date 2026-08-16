# Soft Delete — Design

## Objetivo

Adicionar exclusão lógica aos modelos que herdam de `BaseGlobal`, sem confundir
remoção (`is_deleted`) com inativação operacional (`is_active`). Registros
excluídos não aparecem nas consultas ou APIs usuais, mas permanecem no banco
para auditoria e consulta interna.

## Decisões

### Campos de ciclo de vida

`BaseGlobal` passa a definir os campos booleanos abaixo:

- `is_active`, com padrão `True`: representa inativação operacional e substitui
  o campo legado `ativo`.
- `is_deleted`, com padrão `False`: representa exclusão lógica e será incluído
  em `internal_fields`, portanto não será serializado pelos mecanismos base.

O campo `is_active` não participa da exclusão lógica. Um registro inativo pode
continuar existente e um registro excluído não precisa ter seu estado de
ativação alterado.

`Usuario` já possui `is_active` em `AbstractUser`. A migração consolidará
`usuario.ativo` nesse campo existente — um valor legado falso prevalece — e
removerá a coluna legada, sem criar dois campos de mesmo nome.

### Exclusão e consultas

`BaseGlobal.delete()` será sobrescrito para marcar a própria instância com
`is_deleted=True`, gravando somente esse campo. Assim, `instance.delete()` e o
fluxo padrão de `perform_destroy()` do Django REST Framework executam exclusão
lógica diretamente no modelo.

O queryset padrão filtrará `is_deleted=False`, preservando também o guard de
RLS já provido por `BaseQuerySet`. Um manager `all_objects` permitirá consultas
internas que incluam excluídos. A API e o Django admin usarão o manager padrão,
portanto um registro excluído passará a resultar em 404 e não poderá ser
atualizado ou excluído novamente pelas rotas normais.

O queryset também terá `delete()` sobrescrito para marcar em lote os registros
como excluídos; isso evita que `Model.objects.filter(...).delete()` contorne a
regra central. Este escopo não introduz endpoint nem ação administrativa de
restauração.

### APIs e inativação

As referências de domínio a `ativo` serão renomeadas para `is_active`:
filtros, serializadores, handlers, permissões e admin.

As sobrescritas de `perform_destroy()` de `VinculoViewSet` e `ConviteViewSet`
serão removidas para que usem `Model.delete()`. Isso preserva a inativação como
operação separada e faz com que `DELETE` tenha o mesmo significado para `Time`,
`Vinculo` e `Convite`.

### Unicidade

Toda unicidade dos modelos atuais será condicional a `is_deleted=False`:

- `Organizacao.slug`;
- `(Time.organizacao, Time.nome)`;
- `(Vinculo.organizacao, Vinculo.usuario)`;
- `Convite.token`;
- `Usuario.email`.

Consequentemente, após a exclusão lógica será possível criar um novo registro
ativo com a mesma chave de negócio. As migrações removem as unicidades de campo
existentes antes de criar as `UniqueConstraint` condicionais.

## Migrações

As migrações de `organizacoes` renomearão `ativo`, adicionarão `is_deleted` e
substituirão as restrições de unicidade. A migração de `usuarios` consolidará o
legado `ativo` em `is_active`, removerá a coluna antiga, adicionará
`is_deleted` e tornará `email` condicionalmente único. Os dados existentes
continuarão visíveis porque `is_deleted` inicia como falso.

## Testes de aceitação

- `delete()` em uma instância marca `is_deleted` e não remove a linha física.
- consultas normais omitem excluídos; `all_objects` os encontra.
- `QuerySet.delete()` aplica a mesma regra em lote.
- `DELETE` nas APIs de time, vínculo e convite retorna 204 e oculta o registro.
- inativar continua manipulando somente `is_active`.
- chaves únicas podem ser reutilizadas depois da exclusão lógica.
- a consolidação de usuário preserva usuários antes marcados como inativos.

## Fora do escopo

- Restauração por endpoint ou admin.
- Exclusão física como operação pública.
