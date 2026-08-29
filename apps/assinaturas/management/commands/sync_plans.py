"""Sincroniza de forma explícita o bootstrap local do catálogo de planos."""

from django.core.management.base import BaseCommand, CommandError

from apps.assinaturas.catalogs import PLANOS_BOOTSTRAP, ErroCatalogoPlanos, sincronizar_planos


class Command(BaseCommand):
    help = "Compara o catálogo local com o banco; escreve somente com --apply."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplica as criações e mudanças de flags operacionais.")

    def handle(self, *args, **options):
        aplicar = options["apply"]
        try:
            acoes = sincronizar_planos(PLANOS_BOOTSTRAP, aplicar=aplicar)
        except ErroCatalogoPlanos as exc:
            raise CommandError(str(exc)) from exc

        for acao in acoes:
            self.stdout.write(acao)

        if aplicar:
            resumo = "APLICADO: catálogo sincronizado." if acoes else "APLICADO: sem alterações."
            self.stdout.write(self.style.SUCCESS(resumo))
        else:
            self.stdout.write(self.style.WARNING("DRY-RUN: nenhuma alteração foi aplicada."))
