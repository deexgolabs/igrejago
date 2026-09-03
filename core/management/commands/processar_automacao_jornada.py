"""Automação de jornada do visitante: 1x/dia, casa `Person.pipeline_stage`
+ `pipeline_stage_changed_at` contra as regras (`people.AutomacaoJornada`)
de cada igreja e enfileira uma `WhatsAppMessage` por pessoa que bate
exatamente na regra hoje. Mesmo espírito de `enviar_lembretes.py`: roda
via cron, nunca chama `enviar_whatsapp()` direto.

Casa o dia EXATO (`pipeline_stage_changed_at__date == hoje - dias_depois`),
então rodando 1x/dia não duplica sozinho — mas o comando pode ser
re-executado manualmente no mesmo dia (teste, reprocessamento), daí o
`campaign_label=f"Jornada-{regra.pk}-{pessoa.pk}"` com checagem de
existência antes de criar: é o que garante que não manda duas vezes."""

from datetime import date, timedelta

from django.core.management.base import BaseCommand

from core.models import Church
from core.tenant_context import tenant_context
from notifications.models import WhatsAppInstance, WhatsAppMessage
from people.models import AutomacaoJornada, Person


class Command(BaseCommand):
    help = "Enfileira mensagens de automação de jornada do visitante, de cada igreja (ver processar_fila_whatsapp)."

    def handle(self, *args, **options):
        today = date.today()
        total = 0

        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            with tenant_context(church_config):
                total += self._processar_igreja(church_config, today)

        self.stdout.write(self.style.SUCCESS(f"{total} mensagem(ns) de automação de jornada adicionada(s) à fila."))

    @staticmethod
    def _processar_igreja(church_config, today):
        to_create = []
        instancia_padrao = WhatsAppInstance.padrao()

        for regra in AutomacaoJornada.objects.filter(ativo=True):
            alvo = today - timedelta(days=regra.dias_depois)
            pessoas = Person.objects.filter(
                pipeline_stage=regra.etapa, pipeline_stage_changed_at__date=alvo,
            )
            for pessoa in pessoas:
                if not pessoa.phone:
                    continue
                campaign_label = f"Jornada-{regra.pk}-{pessoa.pk}"
                if WhatsAppMessage.todas_as_igrejas.filter(
                    church=church_config, campaign_label=campaign_label
                ).exists():
                    continue
                mensagem = regra.mensagem.format(nome=pessoa.full_name)
                to_create.append(WhatsAppMessage(
                    church=church_config, person=pessoa, phone=pessoa.whatsapp_number, message=mensagem,
                    instance=instancia_padrao,
                    campaign_label=campaign_label,
                ))

        WhatsAppMessage.objects.bulk_create(to_create)
        return len(to_create)
