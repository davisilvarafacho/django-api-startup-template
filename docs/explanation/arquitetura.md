# Arquitetura

A aplicação usa Django REST Framework com PostgreSQL como banco principal, Redis
para cache e broker, e Celery para trabalho assíncrono. A especificação é gerada
por drf-spectacular e apresentada pelo Scalar.

A documentação segue Diátaxis para separar aprendizado, procedimentos, referência
e decisões arquiteturais.
