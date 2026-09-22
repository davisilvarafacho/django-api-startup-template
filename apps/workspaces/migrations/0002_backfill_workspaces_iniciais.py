from django.db import migrations


def criar_workspaces_iniciais(apps, schema_editor):
    Organizacao = apps.get_model("organizacoes", "Organizacao")
    Vinculo = apps.get_model("organizacoes", "Vinculo")
    Workspace = apps.get_model("workspaces", "Workspace")
    VinculoWorkspace = apps.get_model("workspaces", "VinculoWorkspace")
    using = schema_editor.connection.alias

    for organizacao in Organizacao.objects.using(using).filter(is_deleted=False).iterator():
        workspace = Workspace.objects.using(using).create(
            organizacao_id=organizacao.pk,
            nome="Principal",
            slug="principal",
        )
        vinculos = Vinculo.objects.using(using).filter(
            organizacao_id=organizacao.pk,
            is_active=True,
            is_deleted=False,
        )
        acessos = [VinculoWorkspace(vinculo_id=vinculo.pk, workspace_id=workspace.pk) for vinculo in vinculos]
        VinculoWorkspace.objects.using(using).bulk_create(acessos)
        vinculos.update(current_workspace_id=workspace.pk)


class Migration(migrations.Migration):
    dependencies = [
        ("organizacoes", "0004_vinculo_current_workspace"),
        ("workspaces", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(criar_workspaces_iniciais, migrations.RunPython.noop),
    ]
