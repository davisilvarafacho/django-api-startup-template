from django.db import migrations


CRIAR_GUARDA_ESTADO_INICIAL = """
CREATE OR REPLACE FUNCTION validar_estado_inicial_proposta_comercial()
RETURNS trigger AS $$
BEGIN
    IF NEW.status <> 10
       OR NEW.revisao <> 1
       OR NEW.enviada_em IS NOT NULL
       OR NEW.aceita_em IS NOT NULL
       OR NEW.aceita_por_id IS NOT NULL
       OR NEW.ativada_em IS NOT NULL
       OR NEW.ativada_por_id IS NOT NULL
       OR NEW.justificativa_ativacao <> ''
       OR NEW.recusada_em IS NOT NULL
       OR NEW.recusada_por_id IS NOT NULL
       OR NEW.expirada_em IS NOT NULL
       OR NEW.cancelada_em IS NOT NULL
       OR NEW.cancelada_por_id IS NOT NULL
    THEN
        RAISE EXCEPTION 'O estado inicial da proposta deve ser rascunho na revisão 1, sem atores ou datas terminais.'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER proposta_comercial_estado_inicial
BEFORE INSERT ON proposta_comercial
FOR EACH ROW EXECUTE FUNCTION validar_estado_inicial_proposta_comercial();
"""

REMOVER_GUARDA_ESTADO_INICIAL = """
DROP TRIGGER IF EXISTS proposta_comercial_estado_inicial ON proposta_comercial;
DROP FUNCTION IF EXISTS validar_estado_inicial_proposta_comercial();
"""


def criar_guarda_estado_inicial(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CRIAR_GUARDA_ESTADO_INICIAL)


def remover_guarda_estado_inicial(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REMOVER_GUARDA_ESTADO_INICIAL)


class Migration(migrations.Migration):
    dependencies = [("assinaturas", "0008_endurecer_transicoes_propostas")]

    operations = [migrations.RunPython(criar_guarda_estado_inicial, remover_guarda_estado_inicial)]
