# Política de Segurança

## Reportando uma vulnerabilidade

**Não** abra uma issue pública para vulnerabilidades de segurança.

Envie os detalhes por e-mail para **davi@rafacho.dev** com:

- descrição da vulnerabilidade e do impacto;
- passos para reproduzir;
- versão/commit afetado.

Você receberá uma confirmação em até 72 horas e manteremos você informado sobre
a correção. Pedimos um prazo razoável para correção antes de qualquer divulgação
pública.

## Escopo

Dependências são monitoradas por Dependabot e escaneadas por `pip-audit`,
`bandit` (SAST) e `trivy` (imagem Docker) no CI.
