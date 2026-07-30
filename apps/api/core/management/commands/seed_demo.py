"""Create generic, idempotent demonstration data for local development."""
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organizacoes.models import Organizacao, Papel, Time, Vinculo
from apps.usuarios.models import Usuario


class Command(BaseCommand):
    help = "Cria dados genéricos e idempotentes para demonstração local."

    def add_arguments(self, parser):
        parser.add_argument(
            "--allow-production",
            action="store_true",
            help="Permite deliberadamente o seed quando DJANGO_ENVIRONMENT=production.",
        )

    def handle(self, *args, **options):
        if settings.IN_PRODUCTION and not options["allow_production"]:
            raise CommandError(
                "seed_demo é bloqueado em produção; use --allow-production para confirmar."
            )

        with transaction.atomic():
            estados = self._criar_grafo()

        for entidade, criado in estados:
            estado = "criado" if criado else "já existia"
            self.stdout.write(f"{entidade}: {estado}")

        self.stdout.write(
            self.style.SUCCESS(
                "Credenciais: demo@example.com / demo123456\n"
                "Login: POST /auth/login/\n"
                "Tenant: X-Organization: demo"
            )
        )

    def _criar_grafo(self):
        organizacao, organizacao_criada = Organizacao.objects.get_or_create(
            slug="demo",
            defaults={"nome": "Organização Demo"},
        )
        time, time_criado = Time.objects.get_or_create(
            organizacao=organizacao,
            nome="Time Demo",
        )
        usuario, usuario_criado = Usuario.objects.get_or_create(
            email="demo@example.com",
            defaults={
                "first_name": "Usuário",
                "last_name": "Demo",
                "password": make_password("demo123456"),
                "is_staff": False,
                "is_superuser": False,
            },
        )
        vinculo, vinculo_criado = Vinculo.objects.get_or_create(
            organizacao=organizacao,
            usuario=usuario,
            defaults={"papel": Papel.PROPRIETARIO},
        )
        vinculo.times.add(time)

        return [
            ("Organização Demo", organizacao_criada),
            ("Time Demo", time_criado),
            ("Usuário Demo", usuario_criado),
            ("Vínculo Demo", vinculo_criado),
        ]
