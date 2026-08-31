"""Fase 4 — planos/limites (`core.billing`) e assinatura automática via
Mercado Pago (`core.mercadopago_billing`/webhook). Ver plano em
`.claude/plans/quiet-enchanting-seahorse.md`."""

from unittest.mock import patch

import pytest

from core.billing import pode_adicionar_pessoa, whatsapp_liberado
from core.models import Church
from people.models import Person


@pytest.mark.django_db
class TestBillingHelpers:
    def test_trial_church_has_no_person_limit(self, church):
        church.status = Church.Status.TRIAL
        church.save()
        assert pode_adicionar_pessoa(church) is True

    def test_trial_church_has_whatsapp_liberado(self, church):
        church.status = Church.Status.TRIAL
        church.save()
        assert whatsapp_liberado(church) is True

    def test_basico_plan_blocks_whatsapp(self, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.BASICO
        church.save()
        assert whatsapp_liberado(church) is False

    def test_pro_plan_liberates_whatsapp(self, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.save()
        assert whatsapp_liberado(church) is True

    def test_basico_plan_respects_person_limit(self, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.BASICO
        church.save()
        Person.objects.bulk_create([
            Person(church=church, full_name=f"Pessoa {i}") for i in range(100)
        ])
        assert pode_adicionar_pessoa(church) is False

    def test_pro_plan_has_no_person_limit(self, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.save()
        Person.objects.bulk_create([
            Person(church=church, full_name=f"Pessoa {i}") for i in range(150)
        ])
        assert pode_adicionar_pessoa(church) is True


@pytest.mark.django_db
class TestPersonCreateRespectsLimit:
    def test_pastor_blocked_from_creating_beyond_basico_limit(self, pastor_client, church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.BASICO
        church.save()
        Person.objects.bulk_create([
            Person(church=church, full_name=f"Pessoa {i}") for i in range(100)
        ])

        response = pastor_client.post("/pessoas/novo/", {
            "full_name": "Além do limite", "role": "MEMBER", "status": "ACTIVE", "is_member": "on",
        })
        assert response.status_code == 200  # form_invalid re-renderiza, não redireciona
        assert not Person.objects.filter(full_name="Além do limite").exists()


@pytest.mark.django_db
class TestAssinaturaView:
    def test_pastor_sees_both_plans(self, pastor_client):
        response = pastor_client.get("/assinatura/")
        assert response.status_code == 200
        assert b"B\xc3\xa1sico" in response.content
        assert b"Pro" in response.content

    def test_member_cannot_access(self, member_client):
        response = member_client.get("/assinatura/")
        assert response.status_code == 403

    def test_checkout_without_platform_token_shows_error(self, pastor_client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = ""
        response = pastor_client.post("/assinatura/assinar/pro/")
        assert response.status_code == 302

    def test_checkout_invalid_plan_shows_error(self, pastor_client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        response = pastor_client.post("/assinatura/assinar/inexistente/")
        assert response.status_code == 302

    def test_checkout_failure_is_caught_gracefully(self, pastor_client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        with patch("core.views.criar_assinatura", side_effect=Exception("timeout")):
            response = pastor_client.post("/assinatura/assinar/pro/")
        assert response.status_code == 302


@pytest.mark.django_db
class TestAssinaturaWebhook:
    def test_missing_preapproval_id_is_bad_request(self, client):
        response = client.post("/assinatura/webhook/mercadopago/")
        assert response.status_code == 400

    def test_without_platform_token_is_bad_request(self, client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = ""
        response = client.post("/assinatura/webhook/mercadopago/?id=PRE123")
        assert response.status_code == 400

    def test_authorized_status_activates_church(self, client, settings, church):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        church.status = Church.Status.TRIAL
        church.save()
        fake_response = {
            "status": "authorized",
            "external_reference": f"CHURCH-{church.pk}-pro",
        }
        with patch("core.views.consultar_assinatura", return_value=fake_response):
            response = client.post(f"/assinatura/webhook/mercadopago/?id=PRE{church.pk}")
        assert response.status_code == 200
        church.refresh_from_db()
        assert church.status == Church.Status.ACTIVE
        assert church.plano == "pro"
        assert church.gateway_subscription_id == f"PRE{church.pk}"

    def test_cancelled_status_suspends_church(self, client, settings, church):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.save()
        fake_response = {
            "status": "cancelled",
            "external_reference": f"CHURCH-{church.pk}-pro",
        }
        with patch("core.views.consultar_assinatura", return_value=fake_response):
            response = client.post(f"/assinatura/webhook/mercadopago/?id=PRE{church.pk}")
        assert response.status_code == 200
        church.refresh_from_db()
        assert church.status == Church.Status.SUSPENDED

    def test_unrecognized_external_reference_is_ignored(self, client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        fake_response = {"status": "authorized", "external_reference": "algo-aleatorio"}
        with patch("core.views.consultar_assinatura", return_value=fake_response):
            response = client.post("/assinatura/webhook/mercadopago/?id=PRE999")
        assert response.status_code == 200

    def test_api_failure_returns_502(self, client, settings):
        settings.PLATFORM_MERCADOPAGO_ACCESS_TOKEN = "fake-token"
        with patch("core.views.consultar_assinatura", side_effect=Exception("timeout")):
            response = client.post("/assinatura/webhook/mercadopago/?id=PRE1")
        assert response.status_code == 502
