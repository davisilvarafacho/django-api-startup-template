# Revogação transacional de conta excluída

## Contexto

Uma instância de `Usuario` com exclusão lógica mantém `is_active=True`. Como
tokens resolvem o responsável pelo manager base e os resolvers de autorização
verificam somente atividade, uma sessão, token ou API key previamente emitido
continua utilizável depois de `usuario.delete()`.

## Objetivo

Fazer com que a exclusão lógica de uma conta interrompa imediatamente todo
acesso autenticado, preserve os registros para auditoria e invalide os
snapshots de autorização já armazenados.

## Decisão

`Usuario.delete()` será a entrada canônica e transacional da revogação. Ela
marca a conta como excluída e inativa, revoga as credenciais ainda utilizáveis
e os dispositivos confiáveis, e deixa os sinais de `post_save` invalidarem os
epochs das camadas Django, tenant e Guardian no commit.

O queryset de usuários não fará `update(is_deleted=True)` diretamente. A
exclusão em lote executará o mesmo protocolo por conta, sem emitir sinais ou
contornar a revogação.

Como defesa em profundidade, autenticação, resolvers de permissões e backends
também tratarão `is_deleted=True` como uma conta inelegível, inclusive se uma
linha legada tiver sido excluída antes da correção.

## Fluxo

1. Bloquear a linha do usuário em uma transação curta.
2. Se ela já estiver excluída, retornar sem repetir efeitos.
3. Marcar `is_deleted=True` e `is_active=False` em um único `save()`.
4. Revogar sessões, API keys e pré-autenticações ainda ativas; revogar
   dispositivos confiáveis.
5. No commit, os sinais de usuário invalidam os epochs de autorização.
6. Toda autenticação e autorização posterior recusa a conta excluída, mesmo
   que uma credencial ou entrada de cache anterior ainda exista.

## Fora de escopo

- exclusão/anonimização definitiva de dados de conta;
- revogação de vínculos ou encerramento de organizações;
- alteração do protocolo de exclusão de modelos que não sejam `Usuario`;
- substituir o soft-delete por remoção física.

## Testes de aceite

- `usuario.delete()` torna a conta inativa e excluída, revoga sessões, API
  keys, pré-autenticações e dispositivos confiáveis;
- repetir a exclusão não altera novamente credenciais já revogadas;
- um token emitido antes da exclusão falha em uma requisição HTTP posterior;
- resolvers Django e Guardian e seus backends devolvem nenhuma permissão para
  conta excluída;
- exclusão por queryset segue o mesmo protocolo;
- callbacks de commit invalidam as camadas Django, tenant e Guardian.

## Alternativas rejeitadas

- Rejeitar somente `is_deleted` na autenticação: bloqueia o sintoma, mas deixa
  credenciais e cache em estado ativo.
- Adiar até o ciclo completo de exclusão de conta do Batch 5: mantém a falha
  de segurança atual aberta.
