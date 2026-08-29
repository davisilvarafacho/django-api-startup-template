from django.db import migrations, models


CRIAR_TRIGGERS = r"""
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

    IF OLD.publicada_em IS NOT NULL AND ROW(
        NEW.created_at,
        NEW.created_by_id,
        NEW.is_deleted,
        NEW.plano_id,
        NEW.numero,
        NEW.seats_inclusos,
        NEW.limite_seats_trial,
        NEW.duracao_trial_dias,
        NEW.carencia_pagamento_dias,
        NEW.carencia_excesso_seats_dias,
        NEW.expansao_automatica_seats,
        NEW.recursos,
        NEW.publicada_em
    ) IS DISTINCT FROM ROW(
        OLD.created_at,
        OLD.created_by_id,
        OLD.is_deleted,
        OLD.plano_id,
        OLD.numero,
        OLD.seats_inclusos,
        OLD.limite_seats_trial,
        OLD.duracao_trial_dias,
        OLD.carencia_pagamento_dias,
        OLD.carencia_excesso_seats_dias,
        OLD.expansao_automatica_seats,
        OLD.recursos,
        OLD.publicada_em
    ) THEN
        RAISE EXCEPTION 'Versão de plano publicada é imutável.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER assinaturas_versao_publicada_imutavel
BEFORE UPDATE OR DELETE ON versao_plano
FOR EACH ROW EXECUTE FUNCTION assinaturas_proteger_versao_publicada();

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

    IF (versao_antiga_publicada OR versao_nova_publicada) AND ROW(
        NEW.created_at,
        NEW.created_by_id,
        NEW.is_deleted,
        NEW.versao_plano_id,
        NEW.periodicidade,
        NEW.moeda,
        NEW.valor_base_centavos,
        NEW.valor_seat_centavos
    ) IS DISTINCT FROM ROW(
        OLD.created_at,
        OLD.created_by_id,
        OLD.is_deleted,
        OLD.versao_plano_id,
        OLD.periodicidade,
        OLD.moeda,
        OLD.valor_base_centavos,
        OLD.valor_seat_centavos
    ) THEN
        RAISE EXCEPTION 'Preço de versão publicada é imutável.'
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER assinaturas_preco_publicado_imutavel
BEFORE INSERT OR UPDATE OR DELETE ON preco_plano
FOR EACH ROW EXECUTE FUNCTION assinaturas_proteger_preco_publicado();
"""


REMOVER_TRIGGERS = r"""
DROP TRIGGER IF EXISTS assinaturas_preco_publicado_imutavel ON preco_plano;
DROP FUNCTION IF EXISTS assinaturas_proteger_preco_publicado();
DROP TRIGGER IF EXISTS assinaturas_versao_publicada_imutavel ON versao_plano;
DROP FUNCTION IF EXISTS assinaturas_proteger_versao_publicada();
"""


def criar_triggers_postgresql(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(CRIAR_TRIGGERS)


def remover_triggers_postgresql(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(REMOVER_TRIGGERS)


class Migration(migrations.Migration):
    dependencies = [("assinaturas", "0001_initial")]

    operations = [
        migrations.AddConstraint(
            model_name="versaoplano",
            constraint=models.CheckConstraint(
                condition=models.Q(atual=False) | models.Q(publicada_em__isnull=False, is_deleted=False),
                name="versao_plano_atual_publicada_viva",
            ),
        ),
        migrations.RunPython(criar_triggers_postgresql, remover_triggers_postgresql),
    ]
