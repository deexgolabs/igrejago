"""Gera as escalas do mês por rodízio simples (round-robin, sem solver de
restrição de verdade): para cada `Department` que tem voluntários
(`Person.department`), distribui a lista de voluntários em ordem pelos
domingos do mês, um por domingo, repetindo a lista se sobrar domingo —
pulando quem avisou indisponibilidade naquele domingo específico
(`IndisponibilidadeVoluntario`, ver `_proximo_disponivel`). Pensado pra
rodar 1x/mês via cron/agendador (ex.: dia 25, gerando o mês seguinte) —
não é acionado por nenhuma view do sistema; ajuste manual continua
disponível em `escalas:create`/`escalas:update`.

Idempotente: nunca sobrescreve uma `Escala` que já existe pra aquele
departamento+data (gerada antes ou ajustada manualmente) — só preenche o
que ainda não existe.

NÃO envia nada diretamente — só enfileira uma `WhatsAppMessage` de aviso
por voluntário recém-escalado (mesma fila de sempre; ver
`processar_fila_whatsapp`). O link de confirmação usa `settings.SITE_URL`
em vez de `request.build_absolute_uri` porque um comando de management não
tem `request` nenhum."""

import calendar
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand
from django.urls import reverse

from core.models import Church
from core.tenant_context import tenant_context
from escalas.models import Escala, EscalaVoluntario, IndisponibilidadeVoluntario
from notifications.models import WhatsAppMessage
from people.models import Department, Person


class Command(BaseCommand):
    help = "Gera escalas do mês por rodízio entre os voluntários de cada departamento (um por domingo) e enfileira o aviso de confirmação."

    def add_arguments(self, parser):
        parser.add_argument("--mes", type=int, default=None, help="Mês (1-12). Padrão: mês seguinte ao atual.")
        parser.add_argument("--ano", type=int, default=None, help="Ano. Padrão: ano do mês seguinte ao atual.")

    def handle(self, *args, **options):
        today = date.today()
        if options["mes"]:
            mes = options["mes"]
            ano = options["ano"] or today.year
        else:
            mes, ano = (1, today.year + 1) if today.month == 12 else (today.month + 1, today.year)

        total_escalas = total_mensagens = total_sem_voluntario = 0
        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            with tenant_context(church_config):
                escalas, mensagens, sem_voluntario = self._gerar_igreja(church_config, ano, mes)
            total_escalas += escalas
            total_mensagens += mensagens
            total_sem_voluntario += sem_voluntario

        aviso_indisponiveis = (
            f" {total_sem_voluntario} domingo(s)/departamento(s) ficaram sem voluntário disponível — "
            "ajuste manual em escalas:create."
            if total_sem_voluntario else ""
        )
        self.stdout.write(self.style.SUCCESS(
            f"{total_escalas} escala(s) gerada(s), {total_mensagens} aviso(s) de WhatsApp "
            f"enfileirado(s) — {mes:02d}/{ano}, em todas as igrejas.{aviso_indisponiveis}"
        ))

    @staticmethod
    def _domingos_do_mes(ano, mes):
        cal = calendar.Calendar(firstweekday=6)  # domingo primeiro
        return [d for d in cal.itermonthdates(ano, mes) if d.month == mes and d.weekday() == 6]

    def _gerar_igreja(self, church_config, ano, mes):
        domingos = self._domingos_do_mes(ano, mes)
        escalas_criadas = sem_voluntario = 0
        mensagens = []

        for department in Department.objects.all():
            voluntarios = list(Person.objects.filter(department=department).order_by("full_name"))
            if not voluntarios:
                continue

            # Um único SELECT pra saber quem avisou indisponibilidade em
            # qualquer um dos domingos do mês (`IndisponibilidadeVoluntario`),
            # em vez de uma query por pessoa por domingo.
            indisponiveis = set(
                IndisponibilidadeVoluntario.objects.filter(
                    person__in=voluntarios, date__in=domingos
                ).values_list("person_id", "date")
            )

            for i, domingo in enumerate(domingos):
                if Escala.objects.filter(department=department, date=domingo).exists():
                    continue

                pessoa = self._proximo_disponivel(voluntarios, i, domingo, indisponiveis)
                if pessoa is None:
                    # Todo mundo do departamento avisou indisponibilidade
                    # nesse domingo — pula (não cria Escala vazia) em vez
                    # de escalar alguém indisponível ou quebrar o comando.
                    sem_voluntario += 1
                    continue

                escala = Escala.objects.create(church=church_config, department=department, date=domingo)
                escalas_criadas += 1

                ev = EscalaVoluntario.objects.create(church=church_config, escala=escala, person=pessoa)
                if pessoa.phone:
                    url = f"{settings.SITE_URL}{reverse('escalas:confirmar', args=[ev.confirm_token])}"
                    texto = (
                        f"Você foi escalado(a) para {department.name} — {domingo:%d/%m}. "
                        f"Confirma presença? {url}"
                    )
                    mensagens.append(WhatsAppMessage(
                        church=church_config, person=pessoa, phone=pessoa.whatsapp_number, message=texto,
                        campaign_label=f"Escala-{escala.pk}",
                    ))

        WhatsAppMessage.objects.bulk_create(mensagens)
        return escalas_criadas, len(mensagens), sem_voluntario

    @staticmethod
    def _proximo_disponivel(voluntarios, i, domingo, indisponiveis):
        """A partir da posição `i` do rodízio, tenta cada voluntário em
        ordem (dando a volta na lista) até achar um que NÃO avisou
        indisponibilidade nesse domingo. `None` se ninguém estiver livre
        — a rotação em si não "volta" pro indisponível depois, só pula
        ele NESSE domingo específico."""
        n = len(voluntarios)
        for offset in range(n):
            candidato = voluntarios[(i + offset) % n]
            if (candidato.pk, domingo) not in indisponiveis:
                return candidato
        return None
