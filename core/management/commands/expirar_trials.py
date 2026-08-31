"""Vence o trial de quem passou de `Church.trial_expira_em` sem virar
`ativo` (assinatura confirmada — ver `core.billing`/Fase 4) — vira
`suspenso`, mesmo status usado por assinatura cancelada/pagamento
falhou. Pensado pra rodar 1x/dia via cron (mesmo padrão de
`enviar_lembretes`/`verificar_conexao_whatsapp`). Não mexe em quem já
está `ativo` ou `suspenso` — só quem ainda está em `trial`."""

from datetime import date

from django.core.management.base import BaseCommand

from core.models import Church


class Command(BaseCommand):
    help = "Suspende igrejas cujo trial venceu sem virar uma assinatura ativa."

    def handle(self, *args, **options):
        vencidas = Church.objects.filter(
            status=Church.Status.TRIAL, trial_expira_em__lt=date.today()
        )
        nomes = list(vencidas.values_list("name", flat=True))
        total = vencidas.update(status=Church.Status.SUSPENDED)

        if total:
            self.stdout.write(self.style.WARNING(f"{total} igreja(s) suspensa(s) por trial vencido: {', '.join(nomes)}"))
        else:
            self.stdout.write("Nenhum trial vencido.")
