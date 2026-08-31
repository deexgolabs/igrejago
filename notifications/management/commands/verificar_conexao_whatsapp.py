"""Checa se a instância do WhatsApp de CADA igreja continua conectada e
avisa a equipe por e-mail se caiu — pensado pra rodar via cron a cada
15-30 minutos (ver DEPLOY.md). Sem isso, uma sessão que desconectou
(trocou de aparelho, ficou muito tempo offline etc.) só é percebida
quando alguém nota que as mensagens pararam de sair.

Só manda UM e-mail por queda por igreja (`Church.whatsapp_disconnect_alert_sent`
controla isso) — sem essa trava, cada execução do cron enquanto a conexão
continuar caída mandaria um e-mail novo."""

import logging

from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from core.models import Church
from core.whatsapp import obter_status_conexao

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Verifica a conexão do WhatsApp de cada igreja e avisa por e-mail (uma vez) se caiu."

    def handle(self, *args, **options):
        for config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            self._verificar_igreja(config)

    def _verificar_igreja(self, config):
        if not config.whatsapp_api_configured:
            return

        connected = self._is_connected(config)

        if connected:
            if config.whatsapp_disconnect_alert_sent:
                config.whatsapp_disconnect_alert_sent = False
                config.save(update_fields=["whatsapp_disconnect_alert_sent"])
                self.stdout.write(self.style.SUCCESS(f"[{config.name}] Reconectado — alerta reiniciado."))
            else:
                self.stdout.write(f"[{config.name}] Conectado.")
            return

        self.stdout.write(self.style.WARNING(f"[{config.name}] Desconectado."))
        if config.whatsapp_disconnect_alert_sent:
            self.stdout.write("Alerta já enviado pra esta queda — não envia de novo.")
            return
        if not config.admin_alert_emails:
            self.stdout.write("Nenhum e-mail de alerta configurado em Configurações.")
            return

        recipients = [email.strip() for email in config.admin_alert_emails.split(",") if email.strip()]
        try:
            send_mail(
                subject=f"WhatsApp desconectado — {config.name or 'sua igreja'}",
                message=(
                    "O número de WhatsApp da igreja desconectou e as mensagens da fila "
                    "não estão sendo enviadas.\n\n"
                    "Entre no sistema, em Mensagens → Conectar WhatsApp, e escaneie o QR code de novo."
                ),
                from_email=None,
                recipient_list=recipients,
                fail_silently=False,
            )
            config.whatsapp_disconnect_alert_sent = True
            config.save(update_fields=["whatsapp_disconnect_alert_sent"])
            self.stdout.write(self.style.SUCCESS(f"Alerta enviado para {', '.join(recipients)}."))
        except Exception:
            logger.exception("Falha ao enviar alerta de desconexão do WhatsApp (%s)", config.name)
            self.stdout.write(self.style.ERROR("Falha ao enviar o e-mail de alerta — ver log."))

    @staticmethod
    def _is_connected(config):
        try:
            data = obter_status_conexao(config)
        except Exception:
            # Falha ao checar (servidor fora do ar, timeout etc.) — trata
            # como "não confirmadamente conectado", mesma postura cautelosa
            # de `notifications._connection_context` na tela in-app.
            return False
        estado = data.get("state") or data.get("instance", {}).get("state") or ""
        return estado == "open"
