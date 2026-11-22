"""Testes do admin da cobrança: o que pode ser editado, o que é só leitura e se as telas renderizam."""

from django.contrib.admin.sites import AdminSite
from django.db import connection
from django.urls import reverse

import pytest

from apps.billing.admin import BillingEventAdmin, PlanAdmin, SubscriptionAdmin
from apps.billing.models import BillingEvent, Plan, Subscription
from apps.organizacoes.models import Organizacao
from tests.support.billing import subscribe_organization
from tests.support.usuarios import criar_usuario

FIELDS_LOCKED_AFTER_SAVE = ("slug", "rank", "is_free", "gateway_price_id")


@pytest.mark.django_db
def test_plan_gateway_price_id_read_only_after_save(rf):
    """`gateway_price_id` é editável só na criação; salvo, vira somente leitura."""
    plan_admin = PlanAdmin(Plan, AdminSite())
    request = rf.get("/")

    assert "gateway_price_id" not in plan_admin.get_readonly_fields(request, obj=None)

    plan = Plan.objects.create(
        name="Pro",
        slug="pro-admin",
        rank=1,
        member_limit=10,
        gateway_price_id="price_pro_admin",
    )

    assert "gateway_price_id" in plan_admin.get_readonly_fields(request, obj=plan)


@pytest.mark.django_db
def test_plan_identity_fields_read_only_after_save_but_member_limit_editable(rf):
    """Slug, ordem e gratuito fixam a identidade do plano; o limite de membros continua editável."""
    plan_admin = PlanAdmin(Plan, AdminSite())
    request = rf.get("/")
    plan = Plan.objects.create(name="Pro", slug="pro-fixo", rank=1, member_limit=10, gateway_price_id="price_pro_fixo")

    fields = plan_admin.get_readonly_fields(request, obj=plan)

    for field in FIELDS_LOCKED_AFTER_SAVE:
        assert field in fields
    assert "member_limit" not in fields


@pytest.mark.django_db
def test_plan_identity_fields_editable_when_creating(rf):
    """Na criação, todos os campos fixos ainda podem ser preenchidos."""
    plan_admin = PlanAdmin(Plan, AdminSite())
    request = rf.get("/")

    fields = plan_admin.get_readonly_fields(request, obj=None)

    for field in FIELDS_LOCKED_AFTER_SAVE:
        assert field not in fields


@pytest.mark.parametrize("admin_class", [SubscriptionAdmin, BillingEventAdmin])
def test_subscription_and_billing_event_admin_is_read_only(admin_class, rf):
    """Assinatura e evento são espelho/caixa de entrada: o admin não cria, altera nem apaga."""
    model = Subscription if admin_class is SubscriptionAdmin else BillingEvent
    model_admin = admin_class(model, AdminSite())
    request = rf.get("/")

    assert model_admin.has_add_permission(request) is False
    assert model_admin.has_change_permission(request) is False
    assert model_admin.has_delete_permission(request) is False
    # Ação em lote customizada não passa pelas checagens de criação e alteração: nenhuma pode existir.
    assert model_admin.get_actions(request) == {}


@pytest.mark.django_db
def test_subscription_admin_pages_render_for_staff_without_organization_context(client):
    """Staff abre a listagem e a edição de assinaturas sem contexto de tenant na requisição."""
    organization = Organizacao.objects.create(nome="Acme", slug="acme-admin")
    subscription = subscribe_organization(organization, member_limit=10)
    staff = criar_usuario(is_staff=True, is_superuser=True)
    client.force_login(staff)

    changelist = client.get(reverse("admin:billing_subscription_changelist"))
    edit_page = client.get(reverse("admin:billing_subscription_change", args=[subscription.pk]))

    assert changelist.status_code == 200
    assert edit_page.status_code == 200


@pytest.mark.django_db
def test_subscription_admin_redirects_when_no_organization_exists(client):
    """Sem organização cadastrada não há assinatura a mostrar: a tela redireciona em vez de falhar."""
    staff = criar_usuario(is_staff=True, is_superuser=True)
    client.force_login(staff)

    response = client.get(reverse("admin:billing_subscription_changelist"))

    assert response.status_code == 302
    assert response.url == reverse("admin:index")


@pytest.mark.django_db
def test_subscription_admin_shows_only_the_selected_organization(client):
    """Com a organização A escolhida, a assinatura da B não aparece na listagem nem abre pela URL de edição."""
    organization_a = Organizacao.objects.create(nome="Alpha", slug="alpha-admin")
    organization_b = Organizacao.objects.create(nome="Beta", slug="beta-admin")
    subscription_a = subscribe_organization(organization_a, member_limit=10)
    subscription_b = subscribe_organization(organization_b, member_limit=10)
    client.force_login(criar_usuario(is_staff=True, is_superuser=True))
    changelist_url = reverse("admin:billing_subscription_changelist")

    changelist = client.get(changelist_url, {"organizacao__id__exact": organization_a.pk})
    other_changelist = client.get(changelist_url)
    other_edit_page = client.get(reverse("admin:billing_subscription_change", args=[subscription_b.pk]))
    own_edit_page = client.get(reverse("admin:billing_subscription_change", args=[subscription_a.pk]))

    assert changelist.status_code == 200
    assert [row.pk for row in changelist.context["cl"].result_list] == [subscription_a.pk]
    # A escolha fica na sessão: sem o filtro, a listagem continua só com a organização A.
    assert [row.pk for row in other_changelist.context["cl"].result_list] == [subscription_a.pk]
    # O Django responde objeto inexistente com redirecionamento ao índice, não com 404.
    assert other_edit_page.status_code == 302
    assert other_edit_page.url == reverse("admin:index")
    assert own_edit_page.status_code == 200


@pytest.mark.django_db
def test_subscription_table_has_tenant_rls_policy():
    """A tabela do espelho é isolada por organização no banco: a policy de tenant filtra por `rls.tenant_id`.

    O banco de teste não liga o RLS por padrão, então o teste o liga na própria transação, que o
    teste desfaz ao terminar.
    """
    Subscription.enable_rls()

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT permissive, qual FROM pg_policies WHERE tablename = %s AND policyname = %s",
            [Subscription._meta.db_table, "isolamento_organizacao"],
        )
        policies = cursor.fetchall()
        cursor.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", [Subscription._meta.db_table])
        row_security = cursor.fetchone()

    assert len(policies) == 1
    permissive, qualification = policies[0]
    assert permissive == "PERMISSIVE"
    assert "organizacao_id" in qualification
    assert "rls.tenant_id" in qualification
    assert row_security == (True, True)
