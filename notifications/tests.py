import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from notifications.models import MessageTemplate, WhatsAppMessage
from notifications.views import normalize_phone


class TestNormalizePhone:
    @pytest.mark.parametrize("raw,expected", [
        ("62999998888", "5562999998888"),
        ("(62) 99999-8888", "5562999998888"),
        ("5562999998888", "5562999998888"),
        ("", ""),
    ])
    def test_normalizes_to_e164_with_ddi(self, raw, expected):
        assert normalize_phone(raw) == expected


@pytest.mark.django_db
class TestScheduledMessageCreate:
    def test_creates_queued_message_for_typed_phone(self, pastor_client):
        response = pastor_client.post("/mensagens/nova/", {
            "phone": "62911112222", "message": "Oi, tudo bem?",
        })
        assert response.status_code == 302
        msg = WhatsAppMessage.objects.get()
        assert msg.phone == "5562911112222"
        assert msg.status == WhatsAppMessage.Status.PENDING
        assert msg.scheduled_for is None

    def test_creates_queued_message_for_person(self, pastor_client, person):
        response = pastor_client.post("/mensagens/nova/", {
            "person": person.pk, "message": "Oi {nome}",
        })
        assert response.status_code == 302
        msg = WhatsAppMessage.objects.get()
        assert msg.person == person
        assert msg.phone == person.whatsapp_number

    def test_requires_person_or_phone(self, pastor_client):
        response = pastor_client.post("/mensagens/nova/", {"message": "Oi"})
        assert response.status_code == 200
        assert not WhatsAppMessage.objects.exists()

    def test_future_scheduled_for_is_stored(self, pastor_client):
        future = timezone.now() + timedelta(days=2)
        response = pastor_client.post("/mensagens/nova/", {
            "phone": "62911112222", "message": "Lembrete",
            "scheduled_for": future.strftime("%Y-%m-%dT%H:%M"),
        })
        assert response.status_code == 302
        msg = WhatsAppMessage.objects.get()
        assert msg.scheduled_for is not None
        assert not msg.is_due

    def test_member_cannot_access(self, member_client):
        response = member_client.get("/mensagens/nova/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestMessageQueueListAndCancel:
    def test_pending_message_can_be_cancelled(self, pastor_client, church):
        msg = WhatsAppMessage.objects.create(church=church, phone="5562911112222", message="Oi")
        response = pastor_client.post(f"/mensagens/{msg.pk}/cancelar/")
        assert response.status_code == 302
        msg.refresh_from_db()
        assert msg.status == WhatsAppMessage.Status.CANCELLED

    def test_sent_message_cannot_be_cancelled(self, pastor_client, church):
        msg = WhatsAppMessage.objects.create(
            church=church, phone="5562911112222", message="Oi", status=WhatsAppMessage.Status.SENT
        )
        response = pastor_client.post(f"/mensagens/{msg.pk}/cancelar/")
        assert response.status_code == 404
        msg.refresh_from_db()
        assert msg.status == WhatsAppMessage.Status.SENT

    def test_list_filters_by_status(self, pastor_client, church):
        WhatsAppMessage.objects.create(church=church, phone="5562911112222", message="A", status="SENT")
        WhatsAppMessage.objects.create(church=church, phone="5562911113333", message="B", status="PENDING")

        response = pastor_client.get("/mensagens/?status=SENT")
        assert len(response.context["queue_messages"]) == 1


@pytest.mark.django_db
class TestQueuePrevisaoDeEnvio:
    """Regressão: a coluna "Agendada para" mostrava só "assim que possível"
    pra quase toda mensagem (`scheduled_for` quase sempre em branco), sem
    dar nenhuma pista de quando ela ia sair de verdade — achado num
    relato real de usuário. Ver `MessageQueueListView._anotar_previsao_de_envio`."""

    def test_pending_messages_get_sequential_queue_position(self, pastor_client, church):
        first = WhatsAppMessage.objects.create(church=church, phone="5562911110001", message="Um")
        second = WhatsAppMessage.objects.create(church=church, phone="5562911110002", message="Dois")

        response = pastor_client.get("/mensagens/")
        by_pk = {m.pk: m for m in response.context["queue_messages"]}
        assert by_pk[first.pk].queue_position == 1
        assert by_pk[second.pk].queue_position == 2
        assert by_pk[first.pk].estimated_send_at < by_pk[second.pk].estimated_send_at

    def test_message_beyond_batch_size_lands_in_a_later_cycle(self, pastor_client, church):
        church.whatsapp_batch_size = 1
        church.whatsapp_send_interval_seconds = 6
        church.save()
        first = WhatsAppMessage.objects.create(church=church, phone="5562911110001", message="Um")
        second = WhatsAppMessage.objects.create(church=church, phone="5562911110002", message="Dois")

        response = pastor_client.get("/mensagens/")
        by_pk = {m.pk: m for m in response.context["queue_messages"]}
        # batch_size=1 -> a segunda mensagem só entra no PRÓXIMO ciclo da
        # tarefa contínua (WHATSAPP_QUEUE_CYCLE_SECONDS), não no mesmo lote.
        gap = (by_pk[second.pk].estimated_send_at - by_pk[first.pk].estimated_send_at).total_seconds()
        assert gap >= 50

    def test_sent_message_has_no_estimate(self, pastor_client, church):
        msg = WhatsAppMessage.objects.create(
            church=church, phone="5562911110001", message="Um", status=WhatsAppMessage.Status.SENT
        )
        response = pastor_client.get("/mensagens/")
        by_pk = {m.pk: m for m in response.context["queue_messages"]}
        assert by_pk[msg.pk].estimated_send_at is None

    def test_future_scheduled_message_has_no_estimate_yet(self, pastor_client, church):
        from datetime import timedelta

        from django.utils import timezone

        msg = WhatsAppMessage.objects.create(
            church=church, phone="5562911110001", message="Um",
            scheduled_for=timezone.now() + timedelta(days=1),
        )
        response = pastor_client.get("/mensagens/")
        by_pk = {m.pk: m for m in response.context["queue_messages"]}
        assert by_pk[msg.pk].estimated_send_at is None


@pytest.mark.django_db
class TestProcessarFilaCommand:
    def test_sends_due_messages_and_sleeps_between_them(self, church_config, capsys):
        WhatsAppMessage.objects.create(church=church_config, phone="5562911110001", message="Um")
        WhatsAppMessage.objects.create(church=church_config, phone="5562911110002", message="Dois")
        church_config.whatsapp_send_interval_seconds = 3
        church_config.save()

        with patch("notifications.management.commands.processar_fila_whatsapp.time.sleep") as mock_sleep:
            call_command("processar_fila_whatsapp")

        # 2 mensagens -> 1 intervalo entre elas, nenhum depois da última.
        mock_sleep.assert_called_once_with(3)
        assert WhatsAppMessage.objects.filter(status="SENT").count() == 2
        output = capsys.readouterr().out
        assert "2 enviada(s)" in output

    def test_skips_future_scheduled_messages(self, church_config):
        WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Ainda não",
            scheduled_for=timezone.now() + timedelta(days=1),
        )
        call_command("processar_fila_whatsapp")
        msg = WhatsAppMessage.objects.get()
        assert msg.status == WhatsAppMessage.Status.PENDING

    def test_respects_batch_size(self, church_config):
        WhatsAppMessage.objects.bulk_create([
            WhatsAppMessage(church=church_config, phone=f"556291111{i:04d}", message="x") for i in range(5)
        ])
        church_config.whatsapp_batch_size = 2
        church_config.save()

        with patch("notifications.management.commands.processar_fila_whatsapp.time.sleep"):
            call_command("processar_fila_whatsapp")

        assert WhatsAppMessage.objects.filter(status="SENT").count() == 2
        assert WhatsAppMessage.objects.filter(status="PENDING").count() == 3

    def test_records_error_message_on_failure(self, church_config):
        WhatsAppMessage.objects.create(church=church_config, phone="", message="Sem telefone")
        call_command("processar_fila_whatsapp")
        msg = WhatsAppMessage.objects.get()
        assert msg.status == WhatsAppMessage.Status.FAILED
        assert msg.error_message

    def test_records_external_id_on_success(self, church_config):
        WhatsAppMessage.objects.create(church=church_config, phone="5562911110001", message="Oi")
        with patch(
            "notifications.management.commands.processar_fila_whatsapp.enviar_whatsapp",
            return_value=(True, "", "EVOLUTION-MSG-ID-123"),
        ):
            call_command("processar_fila_whatsapp")
        msg = WhatsAppMessage.objects.get()
        assert msg.external_id == "EVOLUTION-MSG-ID-123"

    def test_retries_failed_message_until_max_retries(self, church_config):
        church_config.whatsapp_max_retries = 2
        church_config.save()
        WhatsAppMessage.objects.create(church=church_config, phone="", message="Sempre falha")

        for _ in range(3):
            call_command("processar_fila_whatsapp")

        msg = WhatsAppMessage.objects.get()
        # 1a tentativa (retry_count ainda 0->1) + 2 retries = para de contar em 2.
        assert msg.retry_count == 2
        assert msg.status == WhatsAppMessage.Status.FAILED

    def test_stops_retrying_once_max_retries_reached(self, church_config):
        church_config.whatsapp_max_retries = 1
        church_config.save()
        msg = WhatsAppMessage.objects.create(
            church=church_config, phone="", message="Falha", status="FAILED", retry_count=1
        )

        call_command("processar_fila_whatsapp")

        msg.refresh_from_db()
        # retry_count já bateu o máximo antes de rodar -> nem tenta de novo.
        assert msg.retry_count == 1

    def test_manual_resend_resets_message_to_pending(self, pastor_client, church_config):
        msg = WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Falhou antes", status="FAILED",
            error_message="erro velho", retry_count=5,
        )
        response = pastor_client.post(f"/mensagens/{msg.pk}/reenviar/")
        assert response.status_code == 302
        msg.refresh_from_db()
        assert msg.status == WhatsAppMessage.Status.PENDING
        assert msg.retry_count == 0
        assert msg.error_message == ""

    def test_cannot_resend_a_pending_message(self, pastor_client, church_config):
        msg = WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Ainda na fila", status="PENDING"
        )
        response = pastor_client.post(f"/mensagens/{msg.pk}/reenviar/")
        assert response.status_code == 404

    def test_sends_email_fallback_after_exhausting_retries(self, church_config, person, mailoutbox):
        person.email = "maria@example.com"
        person.save()
        church_config.whatsapp_max_retries = 1
        church_config.save()
        WhatsAppMessage.objects.create(
            church=church_config, person=person, phone="", message="Mensagem importante",
            status="FAILED", retry_count=0,
        )

        call_command("processar_fila_whatsapp")

        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["maria@example.com"]
        assert "Mensagem importante" in mailoutbox[0].body

    def test_no_email_fallback_without_person_email(self, church_config, mailoutbox):
        church_config.whatsapp_max_retries = 1
        church_config.save()
        WhatsAppMessage.objects.create(
            church=church_config, phone="", message="Sem destinatário", status="FAILED", retry_count=0
        )

        call_command("processar_fila_whatsapp")

        assert len(mailoutbox) == 0

    def test_no_email_fallback_on_first_failure(self, church_config, person, mailoutbox):
        """Só depois de esgotar as tentativas automáticas — a primeira
        falha (status PENDING indo pra FAILED) ainda não é definitiva."""
        person.email = "maria@example.com"
        person.save()
        church_config.whatsapp_max_retries = 3
        church_config.save()
        WhatsAppMessage.objects.create(church=church_config, person=person, phone="", message="Primeira falha")

        call_command("processar_fila_whatsapp")

        assert len(mailoutbox) == 0


@pytest.mark.django_db
class TestMessageTemplateCRUD:
    def test_pastor_can_create_template(self, pastor_client):
        response = pastor_client.post("/mensagens/modelos/novo/", {
            "name": "Culto de domingo", "body": "Oi {nome}, te esperamos domingo!",
        })
        assert response.status_code == 302
        assert MessageTemplate.objects.filter(name="Culto de domingo").exists()

    def test_member_cannot_manage_templates(self, member_client):
        response = member_client.get("/mensagens/modelos/")
        assert response.status_code == 403

    def test_template_can_be_deleted(self, pastor_client, church):
        tpl = MessageTemplate.objects.create(church=church, name="Antigo", body="x")
        response = pastor_client.post(f"/mensagens/modelos/{tpl.pk}/excluir/")
        assert response.status_code == 302
        assert not MessageTemplate.objects.filter(pk=tpl.pk).exists()

    def test_template_appears_as_picker_option_on_campaign_form(self, pastor_client, church):
        MessageTemplate.objects.create(church=church, name="Aviso geral", body="Atenção {nome}!")
        response = pastor_client.get("/pessoas/campanha/")
        assert b"Aviso geral" in response.content


@pytest.mark.django_db
class TestWhatsAppConnectionInApp:
    def test_connection_page_renders(self, pastor_client, church_config):
        response = pastor_client.get("/mensagens/whatsapp/")
        assert response.status_code == 200

    def test_connection_page_shows_only_connect_disconnect_no_technical_fields(self, pastor_client, church_config):
        response = pastor_client.get("/mensagens/whatsapp/")
        assert b"whatsapp_api_url" not in response.content
        assert b"whatsapp_instance" not in response.content
        assert b"apikey" not in response.content.lower()

    def test_connect_without_config_shows_error(self, pastor_client, church_config):
        response = pastor_client.post("/mensagens/whatsapp/conectar/")
        assert response.status_code == 200
        assert "não foi configurada" in response.content.decode()

    def test_disconnect_without_config_redirects_with_error(self, pastor_client, church_config):
        response = pastor_client.post("/mensagens/whatsapp/desconectar/")
        assert response.status_code == 302

    def test_member_cannot_access_connection_page(self, member_client):
        response = member_client.get("/mensagens/whatsapp/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestWhatsAppGatedByEmailConfirmationAndPlan:
    """Fase 2 (confirmação de e-mail) e Fase 4 (plano) — dois motivos
    diferentes pra bloquear a mesma tela de conectar WhatsApp."""

    def test_unconfirmed_email_blocks_connect(self, pastor_client, church):
        church.email_confirmed = False
        church.save()
        response = pastor_client.post("/mensagens/whatsapp/conectar/")
        assert response.status_code == 200
        assert "Confirme o e-mail" in response.content.decode()

    def test_resend_confirmation_email_sends_one(self, pastor_client, pastor_user, church, mailoutbox):
        pastor_user.email = "pastor@example.com"
        pastor_user.save()
        church.email_confirmed = False
        church.save()
        response = pastor_client.post("/mensagens/whatsapp/reenviar-confirmacao/")
        assert response.status_code == 302
        assert len(mailoutbox) == 1

    def test_resend_without_user_email_shows_error_and_sends_nothing(self, pastor_client, church, mailoutbox):
        church.email_confirmed = False
        church.save()
        response = pastor_client.post("/mensagens/whatsapp/reenviar-confirmacao/")
        assert response.status_code == 302
        assert len(mailoutbox) == 0

    def test_resend_when_already_confirmed_sends_nothing(self, pastor_client, church, mailoutbox):
        church.email_confirmed = True
        church.save()
        pastor_client.post("/mensagens/whatsapp/reenviar-confirmacao/")
        assert len(mailoutbox) == 0

    def test_basico_plan_blocks_connect_even_with_confirmed_email(self, pastor_client, church):
        from core.models import Church

        church.email_confirmed = True
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.BASICO
        church.save()
        response = pastor_client.post("/mensagens/whatsapp/conectar/")
        assert response.status_code == 200
        assert "não está incluído no seu plano" in response.content.decode()

    def test_pro_plan_passes_the_plan_gate(self, pastor_client, church):
        from core.models import Church

        church.email_confirmed = True
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.save()
        response = pastor_client.post("/mensagens/whatsapp/conectar/")
        # passa dos dois gates (e-mail + plano) e cai no "não configurado" —
        # não tem EVOLUTION_API_URL/KEY nos testes.
        assert response.status_code == 200
        assert "não foi configurada" in response.content.decode()

    def test_member_cannot_connect_or_disconnect(self, member_client):
        assert member_client.post("/mensagens/whatsapp/conectar/").status_code == 403
        assert member_client.post("/mensagens/whatsapp/desconectar/").status_code == 403


@pytest.mark.django_db
class TestWhatsAppWebhook:
    def test_rejects_without_secret_configured(self, client, church_config):
        response = client.post("/mensagens/webhook/evolution/", data="{}", content_type="application/json")
        assert response.status_code == 403

    def test_rejects_wrong_secret(self, client, church_config):
        church_config.whatsapp_webhook_secret = "correct-secret"
        church_config.save()
        response = client.post(
            "/mensagens/webhook/evolution/", data="{}", content_type="application/json",
            HTTP_X_WEBHOOK_SECRET="wrong-secret",
        )
        assert response.status_code == 403

    def test_updates_delivery_status_on_valid_event(self, client, church_config):
        church_config.whatsapp_webhook_secret = "correct-secret"
        church_config.save()
        msg = WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Oi", status="SENT", external_id="MSG-ID-123",
        )

        payload = {"event": "messages.update", "data": {"key": {"id": "MSG-ID-123"}, "update": {"status": "READ"}}}
        response = client.post(
            "/mensagens/webhook/evolution/", data=json.dumps(payload), content_type="application/json",
            HTTP_X_WEBHOOK_SECRET="correct-secret",
        )
        assert response.status_code == 200
        msg.refresh_from_db()
        assert msg.delivery_status == WhatsAppMessage.DeliveryStatus.READ
        assert msg.read_at is not None

    def test_unmapped_status_is_ignored_without_error(self, client, church_config):
        church_config.whatsapp_webhook_secret = "correct-secret"
        church_config.save()
        WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Oi", status="SENT", external_id="MSG-ID-999"
        )

        payload = {"event": "messages.update", "data": {"key": {"id": "MSG-ID-999"}, "update": {"status": "SERVER_ACK"}}}
        response = client.post(
            "/mensagens/webhook/evolution/", data=json.dumps(payload), content_type="application/json",
            HTTP_X_WEBHOOK_SECRET="correct-secret",
        )
        assert response.status_code == 200
        assert WhatsAppMessage.objects.get().delivery_status == WhatsAppMessage.DeliveryStatus.UNKNOWN

    def test_updates_delivery_status_on_real_flat_payload_shape(self, client, church_config):
        """Formato de verdade, capturado ao vivo contra um servidor
        Evolution v2.3.7 real: `data.keyId`/`data.status` direto, sem
        aninhar em `key`/`update` (diferente do que a doc pública sugeria
        e do formato usado nos testes acima, mantidos por compatibilidade
        com uma versão que aninhe)."""
        church_config.whatsapp_webhook_secret = "correct-secret"
        church_config.save()
        msg = WhatsAppMessage.objects.create(
            church=church_config, phone="5562911110001", message="Oi", status="SENT", external_id="MSG-ID-REAL",
        )

        payload = {
            "event": "messages.update",
            "instance": "igreja-teste",
            "data": {"keyId": "MSG-ID-REAL", "remoteJid": "5562911110001@s.whatsapp.net", "status": "DELIVERY_ACK"},
        }
        response = client.post(
            "/mensagens/webhook/evolution/", data=json.dumps(payload), content_type="application/json",
            HTTP_X_WEBHOOK_SECRET="correct-secret",
        )
        assert response.status_code == 200
        msg.refresh_from_db()
        assert msg.delivery_status == WhatsAppMessage.DeliveryStatus.DELIVERED
        assert msg.delivered_at is not None


@pytest.mark.django_db
class TestPushSubscribe:
    def test_logged_in_user_can_subscribe(self, member_client, member_user):
        from notifications.models import PushSubscription

        payload = {
            "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
            "keys": {"p256dh": "chave-p256dh", "auth": "chave-auth"},
        }
        response = member_client.post(
            "/mensagens/push/inscrever/", data=json.dumps(payload), content_type="application/json",
        )
        assert response.status_code == 204
        sub = PushSubscription.objects.get()
        assert sub.user == member_user
        assert sub.endpoint == payload["endpoint"]

    def test_anonymous_cannot_subscribe(self, client):
        response = client.post("/mensagens/push/inscrever/", data="{}", content_type="application/json")
        assert response.status_code == 302

    def test_invalid_payload_returns_bad_request(self, member_client):
        response = member_client.post(
            "/mensagens/push/inscrever/", data=json.dumps({"nope": True}), content_type="application/json",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestVerificarConexaoWhatsAppCommand:
    """Comando de cron que avisa por e-mail (uma vez por queda) se o
    WhatsApp desconectou — ver notificações.management.commands.verificar_conexao_whatsapp."""

    @staticmethod
    def _configure_connection(settings, church_config, alert_emails=""):
        # `whatsapp_api_url`/`whatsapp_api_key` viraram propriedades lendo
        # de settings (servidor Evolution compartilhado da plataforma) —
        # não são mais campos da igreja, então "configurar" no teste
        # significa mexer em `settings`, não em `church_config` direto.
        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        church_config.whatsapp_instance = "instancia-teste"
        church_config.whatsapp_instance_token = "token-teste"
        church_config.admin_alert_emails = alert_emails
        church_config.save()

    def _mock_status(self, state):
        return patch(
            "notifications.management.commands.verificar_conexao_whatsapp.obter_status_conexao",
            return_value={"state": state},
        )

    def test_not_configured_does_nothing(self, church_config, mailoutbox):
        call_command("verificar_conexao_whatsapp")
        assert len(mailoutbox) == 0

    def test_connected_sends_no_alert_and_clears_flag(self, settings, church_config, mailoutbox):
        self._configure_connection(settings, church_config, alert_emails="dono@example.com")
        church_config.whatsapp_disconnect_alert_sent = True
        church_config.save()

        with self._mock_status("open"):
            call_command("verificar_conexao_whatsapp")

        church_config.refresh_from_db()
        assert church_config.whatsapp_disconnect_alert_sent is False
        assert len(mailoutbox) == 0

    def test_disconnected_sends_exactly_one_alert_across_runs(self, settings, church_config, mailoutbox):
        self._configure_connection(settings, church_config, alert_emails="dono@example.com, secretaria@example.com")

        with self._mock_status("close"):
            call_command("verificar_conexao_whatsapp")
            call_command("verificar_conexao_whatsapp")

        assert len(mailoutbox) == 1
        assert set(mailoutbox[0].to) == {"dono@example.com", "secretaria@example.com"}
        church_config.refresh_from_db()
        assert church_config.whatsapp_disconnect_alert_sent is True

    def test_disconnected_without_alert_email_sends_nothing(self, settings, church_config, mailoutbox):
        self._configure_connection(settings, church_config, alert_emails="")

        with self._mock_status("close"):
            call_command("verificar_conexao_whatsapp")

        assert len(mailoutbox) == 0

    def test_status_check_failure_is_treated_as_disconnected(self, settings, church_config, mailoutbox):
        self._configure_connection(settings, church_config, alert_emails="dono@example.com")

        with patch(
            "notifications.management.commands.verificar_conexao_whatsapp.obter_status_conexao",
            side_effect=Exception("timeout"),
        ):
            call_command("verificar_conexao_whatsapp")

        assert len(mailoutbox) == 1
