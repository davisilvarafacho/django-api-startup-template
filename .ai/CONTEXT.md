# Domínio do template SaaS

Este vocabulário distingue identidade, tenancy, contrato comercial e
faturamento. Os termos abaixo são canônicos em código, documentação e conversas
do projeto.

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

**Papel**:
O nível de autoridade de um Vínculo dentro da Organização.
_Evitar_: perfil, permissão quando o sentido for nível organizacional

**Convite**:
Uma reserva temporária para que um e-mail crie ou use um Vínculo em uma
Organização.
_Evitar_: Vínculo pendente

## Catálogo e contrato

**Plano**:
A identidade estável de uma oferta comercial do catálogo.
_Evitar_: Assinatura, produto do gateway

**Versão de plano**:
Um conjunto concreto e imutável de termos de um Plano disponível para novas
contratações.
_Evitar_: plano do cliente, revisão da Assinatura

**Preço de plano**:
Os valores de uma Versão de plano para uma moeda e periodicidade.
_Evitar_: mensalidade quando a periodicidade puder ser anual

**Assinatura da organização**:
O contrato comercial vigente ou histórico de uma Organização, com seus termos
copiados e independentes do catálogo futuro.
_Evitar_: Plano, assinatura do gateway

**Alteração de assinatura**:
O pedido rastreável para substituir termos de uma Assinatura da organização
depois da confirmação exigida.
_Evitar_: nova assinatura quando o ciclo contratual continua o mesmo

**Proposta comercial**:
Uma oferta enterprise negociada para uma Organização que ainda não concede
acesso por si só.
_Evitar_: Plano customizado, Assinatura ativa

**Trial**:
O estado temporário de experimentação de uma Versão de plano paga.
_Evitar_: plano trial, carência

**Recurso de plano**:
Uma capacidade ou limite tipado concedido pelos termos comerciais.
_Evitar_: feature flag operacional

## Seats e acesso

**Seat contratado**:
Uma unidade de capacidade prevista no contrato da Organização.
_Evitar_: Usuário, convite

**Seat consumido**:
Um Seat ocupado por um Vínculo que conta para a capacidade contratada.
_Evitar_: Seat faturado

**Seat reservado**:
Um Seat temporariamente comprometido por um Convite válido, ainda sem cobrança.
_Evitar_: Seat consumido

**Carência financeira**:
O período adicional de acesso após falha de renovação de um contrato antes
regular.
_Evitar_: Trial, prazo de pagamento inicial

**Carência de excesso de seats**:
O período para corrigir consumo acima da capacidade depois de uma mudança
contratual ou administrativa.
_Evitar_: expansão automática, tolerância para novos convites

**Acesso restrito**:
O resultado calculado que limita uma Organização às operações de regularização.
_Evitar_: Organização inativa, Assinatura encerrada

## Faturamento

**Assinatura do gateway**:
O mapeamento entre uma Assinatura da organização e seu contrato recorrente em
um gateway.
_Evitar_: Assinatura da organização

**Checkout de cobrança**:
Uma tentativa local e idempotente de iniciar uma operação financeira hospedada
pelo gateway.
_Evitar_: pagamento confirmado, pedido

**Fatura de assinatura**:
Uma cobrança normalizada de um período da Assinatura, sem natureza de documento
fiscal.
_Evitar_: nota fiscal, recibo

**Evento de cobrança**:
Um fato assíncrono autenticado e normalizado recebido de um gateway, usado como
gatilho para reconciliação.
_Evitar_: fonte final de verdade, payload bruto
