"""Processa a fila de WhatsApp de TODAS as igrejas ativas, uma de cada
vez. Provider-aware: canal Meta Cloud continua um lote só por igreja
(`Church.whatsapp_batch_size`/`whatsapp_send_interval_seconds`/
`whatsapp_max_retries` — um número só, sem conceito de instância);
canal Evolution processa CADA `WhatsAppInstance` da igreja
separadamente, com o PRÓPRIO lote/intervalo/tentativas — uma igreja
com "WhatsApp da igreja" + "WhatsApp do pastor" manda dos dois números
em paralelo, cada um no seu próprio ritmo, sem um "pegar carona" no
limite do outro. Pensado pra rodar via cron a cada 1-5 minutos — cada
execução processa um lote por igreja/instância e sai; o resto (se
sobrar) fica pra próxima.

Cada igreja é processada dentro de `tenant_context(church)` — o mesmo
`WhatsAppMessage.objects`/`WhatsAppInstance.objects` de sempre já
filtram sozinhos pra igreja atual, sem precisar reescrever a query com
`todas_as_igrejas` espalhado aqui.

Mandar um lote inteiro de uma vez, sem intervalo, é o jeito mais rápido de
um número real ser marcado como spam/banido pelo WhatsApp — esse intervalo
não é um detalhe cosmético, é o motivo desse comando existir separado de
só chamar `enviar_whatsapp()` em loop."""

import time

from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from core.billing import whatsapp_liberado
from core.models import Church
from core.tenant_context import tenant_context
from core.whatsapp import enviar_whatsapp
from notifications.models import WhatsAppInstance, WhatsAppMessage


class Command(BaseCommand):
    help = "Envia as mensagens pendentes (e reenvia as que falharam) da fila de WhatsApp de cada igreja, com intervalo."

    def handle(self, *args, **options):
        total_sent = total_failed = total_emailed = total_processed = 0
        for church_config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            if not church_config.email_confirmed:
                self.stdout.write(f"[{church_config.name}] e-mail não confirmado — pulando.")
                continue
            if not whatsapp_liberado(church_config):
                self.stdout.write(f"[{church_config.name}] WhatsApp não incluído no plano — pulando.")
                continue
            with tenant_context(church_config):
                if church_config.whatsapp_provider == Church.WhatsAppProvider.META_CLOUD:
                    eligible = self._elegiveis(
                        instance=None, max_retries=church_config.whatsapp_max_retries
                    ).order_by("created_at")[: church_config.whatsapp_batch_size]
                    sent, failed, emailed, total = self._processar_lote(
                        church_config, eligible, instance=None,
                        interval_seconds=church_config.whatsapp_send_interval_seconds,
                        max_retries=church_config.whatsapp_max_retries,
                    )
                elif WhatsAppInstance.objects.exists():
                    sent = failed = emailed = total = 0
                    # Mensagens sem instância nenhuma (dado legado de antes
                    # desta mudança, ou algum ponto que esqueceu de setar)
                    # caem na instância padrão da igreja — nunca ficam
                    # paradas pra sempre.
                    padrao = WhatsAppInstance.padrao()
                    for instancia in WhatsAppInstance.objects.all():
                        eligible = self._elegiveis(instance=instancia, max_retries=instancia.max_retries)
                        if instancia == padrao:
                            eligible = eligible | self._elegiveis(instance=None, max_retries=instancia.max_retries)
                        s, f, e, t = self._processar_lote(
                            church_config, eligible.order_by("created_at")[: instancia.batch_size],
                            instance=instancia,
                            interval_seconds=instancia.send_interval_seconds, max_retries=instancia.max_retries,
                        )
                        sent += s
                        failed += f
                        emailed += e
                        total += t
                else:
                    # Igreja no canal Evolution mas ainda sem NENHUMA
                    # instância criada — mesmo assim a fila continua
                    # testável (cai no fallback de console dentro de
                    # `enviar_whatsapp`), usando o ritmo em `Church` de
                    # sempre (mesmos campos reaproveitados do canal Meta).
                    eligible = self._elegiveis(
                        instance=None, max_retries=church_config.whatsapp_max_retries
                    ).order_by("created_at")[: church_config.whatsapp_batch_size]
                    sent, failed, emailed, total = self._processar_lote(
                        church_config, eligible, instance=None,
                        interval_seconds=church_config.whatsapp_send_interval_seconds,
                        max_retries=church_config.whatsapp_max_retries,
                    )
            total_sent += sent
            total_failed += failed
            total_emailed += emailed
            total_processed += total

        self.stdout.write(self.style.SUCCESS(
            f"{total_sent} enviada(s), {total_failed} falharam ({total_emailed} com fallback por e-mail). "
            f"{total_processed} processada(s) neste lote, em todas as igrejas."
        ))

    @staticmethod
    def _elegiveis(*, instance, max_retries):
        return WhatsAppMessage.objects.select_related("meta_template").filter(
            Q(instance=instance)
            & (
                Q(status=WhatsAppMessage.Status.PENDING)
                & (Q(scheduled_for__isnull=True) | Q(scheduled_for__lte=timezone.now()))
                | Q(status=WhatsAppMessage.Status.FAILED, retry_count__lt=max_retries)
            )
        )

    def _processar_lote(self, church_config, mensagens, *, instance, interval_seconds, max_retries):
        mensagens = list(mensagens)
        sent, failed, emailed = 0, 0, 0
        total = len(mensagens)
        for index, msg in enumerate(mensagens):
            is_retry = msg.status == WhatsAppMessage.Status.FAILED
            ok, error, external_id = enviar_whatsapp(
                msg.phone, msg.message, church_config=church_config, instance=instance,
                meta_template=msg.meta_template, template_values=msg.meta_template_values,
            )
            if ok:
                msg.status = WhatsAppMessage.Status.SENT
                msg.sent_at = timezone.now()
                msg.external_id = external_id
                sent += 1
                new_retry_count = msg.retry_count
            else:
                msg.status = WhatsAppMessage.Status.FAILED
                msg.error_message = error
                new_retry_count = msg.retry_count + 1 if is_retry else msg.retry_count
                if is_retry:
                    msg.retry_count = F("retry_count") + 1
                failed += 1
            msg.save(update_fields=["status", "sent_at", "error_message", "external_id", "retry_count"])

            # Depois de esgotar as tentativas automáticas, a mensagem fica
            # FAILED parada pra sempre (só reenvio manual a resgata) — se a
            # pessoa tem e-mail cadastrado, manda por lá como último
            # recurso, pra não depender só de alguém notar na fila.
            if not ok and is_retry and new_retry_count >= max_retries:
                if self._send_email_fallback(msg, church_config):
                    emailed += 1

            is_last = index == total - 1
            if not is_last:
                time.sleep(interval_seconds)

        return sent, failed, emailed, total

    @staticmethod
    def _send_email_fallback(msg, church_config):
        if not (msg.person and msg.person.email):
            return False
        try:
            send_mail(
                subject=f"Mensagem de {church_config.name or 'sua igreja'}",
                message=msg.message,
                from_email=None,
                recipient_list=[msg.person.email],
                fail_silently=False,
            )
            return True
        except Exception:
            return False
