"""Lembretes automáticos diários de TODAS as igrejas ativas: aniversário
do dia e reunião de célula do dia. Pensado para rodar 1x/dia via
cron/agendador — não é acionado por nenhuma view do sistema.

Cada igreja é processada dentro de `tenant_context(church)` — `Person`/
`Cell`/`WhatsAppMessage` já filtram sozinhos pra igreja atual.

NÃO envia nada diretamente — só enfileira uma `WhatsAppMessage` por
lembrete (mesma fila da campanha em massa e da mensagem avulsa). Quem
manda de verdade, respeitando o intervalo entre envios, é o comando
`processar_fila_whatsapp` — rode os dois no cron, este primeiro."""

from datetime import date

from django.core.management.base import BaseCommand

from cells.models import Cell
from core.models import Church
from core.tenant_context import tenant_context
from notifications.models import WhatsAppMessage
from people.models import Person


class Command(BaseCommand):
    help = "Enfileira lembretes de aniversário e de reunião de célula do dia, de cada igreja (ver processar_fila_whatsapp)."

    def handle(self, *args, **options):
        today = date.today()
        total_birthdays = total_cell_reminders = 0

        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            with tenant_context(church_config):
                birthdays, cell_reminders = self._enfileirar_igreja(church_config, today)
            total_birthdays += birthdays
            total_cell_reminders += cell_reminders

        self.stdout.write(
            self.style.SUCCESS(
                f"{total_birthdays} lembrete(s) de aniversário e "
                f"{total_cell_reminders} lembrete(s) de célula adicionados à fila, em todas as igrejas."
            )
        )

    @staticmethod
    def _enfileirar_igreja(church_config, today):
        to_create = []

        for person in Person.objects.filter(birth_date__month=today.month, birth_date__day=today.day):
            if not person.phone:
                continue
            message = church_config.whatsapp_birthday_template.format(
                nome=person.full_name, pastor=church_config.pastor_name or "a liderança"
            )
            to_create.append(WhatsAppMessage(
                church=church_config, person=person, phone=person.whatsapp_number, message=message,
                campaign_label="Lembrete de aniversário",
            ))
        birthdays_queued = len(to_create)

        for cell in Cell.objects.filter(is_active=True, meeting_weekday=today.weekday()):
            message = (
                f"Lembrete: hoje tem reunião da célula \"{cell.name}\""
                f"{f' às {cell.meeting_time}' if cell.meeting_time else ''}. Te esperamos! 🙏"
            )
            for member in cell.members.all():
                if member.phone:
                    to_create.append(WhatsAppMessage(
                        church=church_config, person=member, phone=member.whatsapp_number, message=message,
                        campaign_label=f"Lembrete de célula — {cell.name}",
                    ))
        cell_reminders_queued = len(to_create) - birthdays_queued

        WhatsAppMessage.objects.bulk_create(to_create)
        return birthdays_queued, cell_reminders_queued
