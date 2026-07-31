"""Reconcilia o token customizado com instalações que usavam Knox puro."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def populate_token_uuids(apps, schema_editor):
    """Gera UUIDs distintos para tokens existentes antes da restrição única."""
    auth_token = apps.get_model("autenticacao", "AuthToken")
    for token in auth_token.objects.filter(uuid__isnull=True).iterator():
        token.uuid = uuid.uuid4()
        token.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("autenticacao", "0002_tokenmetadata_type"),
        ("organizacoes", "0002_soft_delete"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                DO $$
                BEGIN
                    IF to_regclass('public.auth_token') IS NULL
                       AND to_regclass('public.knox_authtoken') IS NOT NULL THEN
                        ALTER TABLE knox_authtoken RENAME TO auth_token;
                        ALTER TABLE auth_token RENAME COLUMN user_id TO responsavel_id;
                        ALTER TABLE auth_token RENAME COLUMN created TO created_at;
                    END IF;

                    IF to_regclass('public.auth_token') IS NOT NULL THEN
                        ALTER TABLE auth_token
                            ADD COLUMN IF NOT EXISTS type smallint NOT NULL DEFAULT 1;
                        ALTER TABLE auth_token
                            ADD COLUMN IF NOT EXISTS scopes jsonb NOT NULL DEFAULT '[]'::jsonb;
                    END IF;
                END $$;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AddField(
            model_name="authtoken",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="criado por",
            ),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="last_modified_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now, verbose_name="última alteração em"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="authtoken",
            name="uuid",
            field=models.UUIDField(blank=True, db_index=True, editable=False, null=True, verbose_name="UUID"),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="name",
            field=models.CharField(blank=True, default="", max_length=100, verbose_name="nome"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="authtoken",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="api_keys",
                to="organizacoes.organizacao",
                verbose_name="organização",
            ),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="revoked_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="revogado em"),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="revoked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="revogado por",
            ),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="suspended_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="suspenso em"),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="suspended_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
                verbose_name="suspenso por",
            ),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="suspension_reason",
            field=models.CharField(blank=True, max_length=255, verbose_name="motivo da suspensão"),
        ),
        migrations.AddField(
            model_name="authtoken",
            name="replaced_by",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="replaces",
                to="autenticacao.authtoken",
                verbose_name="substituído por",
            ),
        ),
        migrations.AddField(
            model_name="tokenmetadata",
            name="reauthenticated_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Última vez que esta sessão confirmou a identidade (senha/MFA).",
                null=True,
                verbose_name="Reautenticado em",
            ),
        ),
        migrations.RemoveField(
            model_name="tokenmetadata",
            name="scopes",
        ),
        migrations.RemoveField(
            model_name="tokenmetadata",
            name="type",
        ),
        migrations.RunPython(populate_token_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="authtoken",
            name="uuid",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True, verbose_name="UUID"),
        ),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE auth_token DROP CONSTRAINT IF EXISTS auth_token_ephemeral_requires_expiry;",
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.RemoveConstraint(
                    model_name="authtoken",
                    name="auth_token_ephemeral_requires_expiry",
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="authtoken",
            constraint=models.CheckConstraint(
                condition=(models.Q(("type", 999)) & models.Q(("organization__isnull", False))) | (~models.Q(("type", 999)) & models.Q(("organization__isnull", True))),
                name="auth_token_api_key_exige_organizacao",
            ),
        ),
        migrations.AddConstraint(
            model_name="authtoken",
            constraint=models.CheckConstraint(
                condition=~models.Q(("type", 999)) | (models.Q(("created_by__isnull", False)) & ~models.Q(("name", ""))),
                name="auth_token_api_key_exige_nome_e_criador",
            ),
        ),
        migrations.AddConstraint(
            model_name="authtoken",
            constraint=models.CheckConstraint(
                condition=models.Q(("type", 999)) | (models.Q(("name", "")) & models.Q(("scopes", []))),
                name="auth_token_sessao_sem_campos_de_api_key",
            ),
        ),
        migrations.AlterModelOptions(
            name="authtoken",
            options={
                "ordering": ("-created_at",),
                "permissions": [
                    ("view_apikey", "Pode ver API keys"),
                    ("add_apikey", "Pode criar API keys"),
                    ("change_apikey", "Pode alterar API keys"),
                    ("delete_apikey", "Pode revogar API keys"),
                    ("rotate_apikey", "Pode rotacionar API keys"),
                ],
                "verbose_name": "Token de autenticação",
                "verbose_name_plural": "Tokens de autenticação",
            },
        ),
        migrations.AlterModelOptions(
            name="tokenmetadata",
            options={
                "ordering": ["-last_used"],
                "permissions": [("grant_unrestricted_apikey", "Pode conceder API keys com scope irrestrito (*)")],
                "verbose_name": "Metadado de token",
                "verbose_name_plural": "Metadados de tokens",
            },
        ),
    ]
