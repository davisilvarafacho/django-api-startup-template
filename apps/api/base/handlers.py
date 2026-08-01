def ativar_registro(registro):
    registro.is_active = True
    registro.save(update_fields=["is_active"])


def inativar_registro(registro):
    registro.is_active = False
    registro.save(update_fields=["is_active"])
