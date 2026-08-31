"""Fase 2 — cadastro público de igreja, confirmação de e-mail, trial e
suspensão. Ver plano em `.claude/plans/quiet-enchanting-seahorse.md`."""

from datetime import date, timedelta

import pytest
from django.core import mail
from django.core.management import call_command

from accounts.models import User
from core.models import Church
from core.tokens import gerar_token_confirmacao


@pytest.mark.django_db
class TestChurchSignup:
    def test_creates_church_in_trial_and_logs_pastor_in(self, client):
        response = client.post("/cadastro-igreja/", {
            "church_name": "Igreja Nova", "pastor_name": "Pastor Novo",
            "username": "pastornovo", "email": "pastor@novo.com", "password": "Senha-Forte-123",
        })
        assert response.status_code == 302

        church = Church.objects.get(name="Igreja Nova")
        assert church.status == Church.Status.TRIAL
        assert church.trial_expira_em == date.today() + timedelta(days=30)
        assert church.email_confirmed is False

        user = User.objects.get(username="pastornovo")
        assert user.church_id == church.pk
        assert user.role == User.Role.PASTOR

        # já autenticado — o dashboard responde sem precisar logar de novo
        dashboard = client.get("/")
        assert dashboard.status_code == 200

    def test_sends_confirmation_email(self, client):
        client.post("/cadastro-igreja/", {
            "church_name": "Igreja Dois", "pastor_name": "Pastor",
            "username": "pastordois", "email": "pastor2@example.com", "password": "Senha-Forte-123",
        })
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["pastor2@example.com"]

    def test_duplicate_username_is_rejected(self, client, pastor_user):
        response = client.post("/cadastro-igreja/", {
            "church_name": "Igreja X", "pastor_name": "P",
            "username": "pastor",  # já existe — fixture `pastor_user`
            "email": "x@example.com", "password": "Senha-Forte-123",
        })
        assert response.status_code == 200
        assert not Church.objects.filter(name="Igreja X").exists()

    def test_honeypot_pretends_success_without_creating_anything(self, client):
        response = client.post("/cadastro-igreja/", {
            "church_name": "Bot Igreja", "pastor_name": "Bot", "username": "botuser",
            "email": "bot@example.com", "password": "Senha-Forte-123",
            "website": "http://spam.example.com",
        })
        assert response.status_code == 302
        assert not Church.objects.filter(name="Bot Igreja").exists()
        assert not User.objects.filter(username="botuser").exists()


@pytest.mark.django_db
class TestConfirmEmail:
    def test_valid_token_confirms_the_church(self, client, church):
        church.email_confirmed = False
        church.save()
        token = gerar_token_confirmacao(church)

        response = client.get(f"/cadastro-igreja/confirmar/{token}/")
        assert response.status_code == 200
        church.refresh_from_db()
        assert church.email_confirmed is True

    def test_invalid_token_shows_error_without_confirming(self, client, church):
        church.email_confirmed = False
        church.save()
        response = client.get("/cadastro-igreja/confirmar/token-invalido/")
        assert response.status_code == 400
        church.refresh_from_db()
        assert church.email_confirmed is False


@pytest.mark.django_db
class TestExpirarTrialsCommand:
    def test_expired_trial_gets_suspended(self, church):
        church.status = Church.Status.TRIAL
        church.trial_expira_em = date.today() - timedelta(days=1)
        church.save()

        call_command("expirar_trials")

        church.refresh_from_db()
        assert church.status == Church.Status.SUSPENDED

    def test_trial_still_within_window_is_untouched(self, church):
        church.status = Church.Status.TRIAL
        church.trial_expira_em = date.today() + timedelta(days=5)
        church.save()

        call_command("expirar_trials")

        church.refresh_from_db()
        assert church.status == Church.Status.TRIAL

    def test_already_active_church_is_untouched(self, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.trial_expira_em = date.today() - timedelta(days=100)
        church.save()

        call_command("expirar_trials")

        church.refresh_from_db()
        assert church.status == Church.Status.ACTIVE


@pytest.mark.django_db
class TestSuspendedChurchIsBlocked:
    def test_suspended_church_redirects_to_conta_suspensa(self, pastor_client, church):
        church.status = Church.Status.SUSPENDED
        church.save()
        response = pastor_client.get("/pessoas/")
        assert response.status_code == 302
        assert response.url == "/conta-suspensa/"

    def test_conta_suspensa_page_itself_is_reachable(self, pastor_client, church):
        church.status = Church.Status.SUSPENDED
        church.save()
        response = pastor_client.get("/conta-suspensa/")
        assert response.status_code == 200

    def test_assinatura_stays_reachable_while_suspended(self, pastor_client, church):
        church.status = Church.Status.SUSPENDED
        church.save()
        response = pastor_client.get("/assinatura/")
        assert response.status_code == 200

    def test_trial_church_is_not_blocked(self, pastor_client, church):
        church.status = Church.Status.TRIAL
        church.save()
        response = pastor_client.get("/pessoas/")
        assert response.status_code == 200
