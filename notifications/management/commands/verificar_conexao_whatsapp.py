"""Checa se CADA `WhatsAppInstance` (Evolution) continua conectada e
avisa a equipe por e-mail se caiu — pensado pra rodar via cron a cada
15-30 minutos (ver DEPLOY.md). Sem isso, uma sessão que desconectou
(trocou de aparelho, ficou muito tempo offline etc.) só é percebida
quando alguém nota que as mensagens pararam de sair. Canal Meta Cloud
não tem "conexão" nesse sentido (não é QR/sessão) — não entra aqui.

Só manda UM e-mail por queda por INSTÂNCIA (`WhatsAppInstance.disconnect_alert_sent`
controla isso) — sem essa trava, cada execução do cron enquanto a
conexão continuar caída mandaria um e-mail novo. Uma igreja com 2
números avisa separadamente pra cada um."""

import logging

from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from core.models import Church
from core.tenant_context import tenant_context
from core.whatsapp import obter_status_conexao
from notifications.models import WhatsAppInstance

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Verifica a conexão de cada instância de WhatsApp (Evolution) e avisa por e-mail (uma vez) se caiu."

    def handle(self, *args, **options):
        for config in Church.objects.exclude(status=Church.Status.SUSPENDED):
            if config.whatsapp_provider != Church.WhatsAppProvider.EVOLUTION:
                continue
            with tenant_context(config):
                for instancia in WhatsAppInstance.objects.all():
                    self._verificar_instancia(config, instancia)

    def _verificar_instancia(self, config, instancia):
        if not instancia.esta_configurada:
            return

        connected = self._is_connected(instancia)
        rotulo = f"{config.name} — {instancia.name}"

        if connected:
            if instancia.disconnect_alert_sent:
                instancia.disconnect_alert_sent = False
                instancia.save(update_fields=["disconnect_alert_sent"])
                self.stdout.write(self.style.SUCCESS(f"[{rotulo}] Reconectado — alerta reiniciado."))
            else:
                self.stdout.write(f"[{rotulo}] Conectado.")
            return

        self.stdout.write(self.style.WARNING(f"[{rotulo}] Desconectado."))
        if instancia.disconnect_alert_sent:
            self.stdout.write("Alerta já enviado pra esta queda — não envia de novo.")
            return
        if not config.admin_alert_emails:
            self.stdout.write("Nenhum e-mail de alerta configurado em Configurações.")
            return

        recipients = [email.strip() for email in config.admin_alert_emails.split(",") if email.strip()]
        try:
            send_mail(
                subject=f"WhatsApp desconectado — {rotulo}",
                message=(
                    f'O número "{instancia.name}" desconectou e as mensagens da fila que saem por ele '
                    "não estão sendo enviadas.\n\n"
                    "Entre no sistema, em Mensagens → WhatsApp, e escaneie o QR code de novo."
                ),
                from_email=None,
                recipient_list=recipients,
                fail_silently=False,
            )
            instancia.disconnect_alert_sent = True
            instancia.save(update_fields=["disconnect_alert_sent"])
            self.stdout.write(self.style.SUCCESS(f"Alerta enviado para {', '.join(recipients)}."))
        except Exception:
            logger.exception("Falha ao enviar alerta de desconexão do WhatsApp (%s)", rotulo)
            self.stdout.write(self.style.ERROR("Falha ao enviar o e-mail de alerta — ver log."))

    @staticmethod
    def _is_connected(instancia):
        try:
            data = obter_status_conexao(instancia)
        except Exception:
            # Falha ao checar (servidor fora do ar, timeout etc.) — trata
            # como "não confirmadamente conectado", mesma postura cautelosa
            # de `notifications._connection_context` na tela in-app.
            return False
        estado = data.get("state") or data.get("instance", {}).get("state") or ""
        return estado == "open"
