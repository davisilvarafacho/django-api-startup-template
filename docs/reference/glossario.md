# Glossário

Vocabulário canônico do template. Estes termos valem em código, documentação e
conversas do projeto — a coluna _Evitar_ de cada verbete lista os sinônimos que
causam ambiguidade e que **não** devem ser usados.

O glossário distingue identidade, tenancy e autorização de campos.

!!! note "Vocabulário implementado e planejado"
    Nem todo termo aqui existe em código. As seções marcadas com **(planejado)**
    fixam o vocabulário de domínios já desenhados em spec, mas ainda sem model
    correspondente — estão aqui para que o nome já nasça estável quando a
    implementação chegar. Hoje existem em código: Usuário, Organização, Vínculo,
    Papel, Convite e Time.

## Identidade e tenancy

**Usuário**:
A pessoa identificada pelo sistema e capaz de autenticar-se por um ou mais
meios.
_Evitar_: cliente, tenant

**Conta**:
O ciclo de acesso de um Usuário, incluindo verificação, desativação,
reativação e exclusão.
_Evitar_: Organização, Assinatura

**Identidade externa**:
A ligação entre um Usuário e o identificador imutável emitido por um provedor
de identidade.
_Evitar_: conta social, token social

**Organização**:
O tenant que agrupa pessoas, dados de negócio e um contrato comercial.
_Evitar_: conta, cliente quando o sentido for ambíguo

**Vínculo**:
A associação de um Usuário a uma Organização, com um Papel e um estado de
atividade.
_Evitar_: usuário da organização, membership

**Workspace**:
O recorte de subtenancy dentro de uma Organização. Um registro com Workspace
fica visível apenas nos contextos autorizados; `NULL` indica que o registro é
compartilhado pela Organização.
_Evitar_: Organização, Time, filial quando a semântica for de isolamento

**Workspace atual**:
O Workspace persistido em `Vinculo.current_workspace`, usado como padrão para
consultas e escritas humanas. É compartilhado entre sessões e dispositivos do
mesmo Vínculo.
_Evitar_: Workspace da sessão, último Workspace do navegador

**Seleção de visualização**:
Conjunto persistido de Workspaces marcados em `VinculoWorkspace` para aparecer
nas consultas humanas. Não substitui o Workspace atual e não altera a
Organização do Vínculo.
_Evitar_: filtro local, Workspace ativo

**Papel**:
O nível de autoridade de um Vínculo dentro da Organização.
_Evitar_: perfil, permissão quando o sentido for nível organizacional

**Convite**:
Uma reserva temporária para que um e-mail crie ou use um Vínculo em uma
Organização.
_Evitar_: Vínculo pendente

## Workspaces e autorização de campos

**Política de escrita de campo**:
Regra de uma organização que protege um campo gravável de um recurso e define o
papel mínimo necessário para alterá-lo.
_Evitar_: Política de edição, permissão do campo, bloqueio do serializer

**Concessão de campo**:
Exceção individual que permite a um usuário editar um campo protegido mesmo sem
alcançar o papel mínimo de sua política.
_Evitar_: Papel especial, liberação global

**Gestão de acesso a campos**:
Autoridade plena, delegável dentro de uma organização, para administrar políticas
de edição e concessões de campo, independentemente do acesso do próprio gestor
aos campos.
_Evitar_: Papel de administrador

**Campo aberto**:
Campo gravável sem política de escrita na organização atual; preserva as regras
normais de autorização do endpoint.
_Evitar_: Campo público, campo sem segurança

## Convenção HTTP

Rotas de domínio usam termos em português, no plural e em `snake_case`. Por
exemplo: `/recursos_exemplos/`, `/recursos_exemplos/itens_relacionados/` e
`/recursos_exemplos/{id}/acoes_disponiveis/`.
