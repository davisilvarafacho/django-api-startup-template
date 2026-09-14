from django.db import migrations


FUNCOES_COM_IDENTIDADE_PROTEGIDA = r"""
CREATE OR REPLACE FUNCTION assinaturas_proteger_versao_publicada()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.publicada_em IS NOT NULL THEN
            RAISE EXCEPTION 'Versão de plano publicada é imutável.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.publicada_em IS NOT NULL
       AND (to_jsonb(NEW) - ARRAY['atual', 'is_active', 'last_modified_at'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['atual', 'is_active', 'last_modified_at']) THEN
        RAISE EXCEPTION 'Versão de plano publicada é imutável.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION assinaturas_proteger_preco_publicado()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    versao record;
    versao_antiga_publicada boolean := false;
    versao_nova_publicada boolean := false;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT publicada_em IS NOT NULL
          INTO versao_nova_publicada
          FROM versao_plano
         WHERE id = NEW.versao_plano_id
           FOR UPDATE;

        IF versao_nova_publicada THEN
            RAISE EXCEPTION 'Preço de versão publicada é imutável.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        SELECT publicada_em IS NOT NULL
          INTO versao_antiga_publicada
          FROM versao_plano
         WHERE id = OLD.versao_plano_id
           FOR UPDATE;

        IF versao_antiga_publicada THEN
            RAISE EXCEPTION 'Preço de versão publicada é imutável.'
                USING ERRCODE = 'integrity_constraint_violation';
        END IF;
        RETURN OLD;
    END IF;

    FOR versao IN
        SELECT id, publicada_em
          FROM versao_plano
         WHERE id IN (OLD.versao_plano_id, NEW.versao_plano_id)
         ORDER BY id
           FOR UPDATE
    LOOP
        IF versao.id = OLD.versao_plano_id THEN
            versao_antiga_publicada := versao.publicada_em IS NOT NULL;
        END IF;
        IF versao.id = NEW.versao_plano_id THEN
            versao_nova_publicada := versao.publicada_em IS NOT NULL;
        END IF;
    END LOOP;

    IF (versao_antiga_publicada OR versao_nova_publicada)
       AND (to_jsonb(NEW) - ARRAY['is_active', 'last_modified_at'])
           IS DISTINCT FROM
           (to_jsonb(OLD) - ARRAY['is_active', 'last_modified_at']) THEN
        RAISE EXCEPTION 'Preço de versão publicada é imutável.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;
"""


FUNCOES_0002 = FUNCOES_COM_IDENTIDADE_PROTEGIDA.replace(
    "ARRAY['atual', 'is_active', 'last_modified_at']",
    "ARRAY['id', 'atual', 'is_active', 'last_modified_at']",
).replace(
    "ARRAY['is_active', 'last_modified_at']",
    "ARRAY['id', 'is_active', 'last_modified_at']",
)


def proteger_identidade_postgresql(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FUNCOES_COM_IDENTIDADE_PROTEGIDA)


def restaurar_funcoes_0002(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(FUNCOES_0002)


class Migration(migrations.Migration):
    dependencies = [("assinaturas", "0002_proteger_catalogo_publicado")]

    operations = [migrations.RunPython(proteger_identidade_postgresql, restaurar_funcoes_0002)]
