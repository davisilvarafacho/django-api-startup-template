# Revogação de Trusted Devices — Design

Alterações de e-mail ou telefone detectadas em `Usuario.save()` revogam todos
os dispositivos confiáveis do usuário após o commit. A confirmação de um novo
fator MFA chama a mesma revogação; remoção de fator e reset administrativo já
possuem essa garantia. Alterações por `QuerySet.update()` não integram este
contrato e não são usadas para contatos no projeto.
