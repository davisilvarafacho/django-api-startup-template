# Contexto da API

Este glossário fixa os termos de domínio usados pelos recursos compartilhados da API.

## Autorização de campos

**Política de escrita de campo**:
Regra de uma organização que protege um campo gravável de um recurso e define o papel mínimo necessário para alterá-lo.
_Evitar_: Política de edição, permissão do campo, bloqueio do serializer

**Concessão de campo**:
Exceção individual que permite a um usuário editar um campo protegido mesmo sem alcançar o papel mínimo de sua política.
_Evitar_: Papel especial, liberação global

**Gestão de acesso a campos**:
Autoridade plena, delegável dentro de uma organização, para administrar políticas de edição e concessões de campo, independentemente do acesso do próprio gestor aos campos.
_Evitar_: Papel de administrador

**Campo aberto**:
Campo gravável sem política de escrita na organização atual; preserva as regras normais de autorização do endpoint.
_Evitar_: Campo público, campo sem segurança

## Convenção HTTP

Rotas de domínio usam termos em português, no plural e em `snake_case`. Por
exemplo: `/recursos_exemplos/`, `/recursos_exemplos/itens_relacionados/` e
`/recursos_exemplos/{id}/acoes_disponiveis/`.
