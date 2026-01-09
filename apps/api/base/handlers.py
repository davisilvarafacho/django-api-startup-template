def ativar_registro(registro):
    registro.ativo = True
    registro.save()


def inativar_registro(registro):
    registro.ativo = False
    registro.save()
