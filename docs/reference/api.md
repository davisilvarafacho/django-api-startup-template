# API

A especificação OpenAPI é exposta em `/api/schema/` e a referência interativa
Scalar em `/api/docs/`.

Para validar o schema localmente:

```bash
uv run python manage.py spectacular --validate --file schema.yml
```
