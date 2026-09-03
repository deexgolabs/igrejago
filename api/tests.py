import json

import pytest

from people.models import Person


@pytest.mark.django_db
class TestApiAuth:
    def test_missing_authorization_header_is_401(self, client):
        response = client.get("/api/pessoas/")
        assert response.status_code == 401

    def test_invalid_key_is_401(self, client):
        response = client.get("/api/pessoas/", HTTP_AUTHORIZATION="Bearer chave-errada")
        assert response.status_code == 401

    def test_valid_key_returns_church_scoped_data(self, client, church, outra_church):
        church.api_key = "chave-valida-123"
        church.save()
        Person.objects.create(church=church, full_name="Da Minha Igreja")
        Person.objects.create(church=outra_church, full_name="De Outra Igreja")

        response = client.get("/api/pessoas/", HTTP_AUTHORIZATION="Bearer chave-valida-123")
        assert response.status_code == 200
        data = response.json()
        names = [p["full_name"] for p in data["results"]]
        assert "Da Minha Igreja" in names
        assert "De Outra Igreja" not in names


@pytest.mark.django_db
class TestApiPagination:
    def test_page_size_is_respected(self, client, church):
        church.api_key = "chave-paginacao"
        church.save()
        for i in range(5):
            Person.objects.create(church=church, full_name=f"Pessoa {i}")

        response = client.get("/api/pessoas/?page_size=2", HTTP_AUTHORIZATION="Bearer chave-paginacao")
        data = response.json()
        assert len(data["results"]) == 2
        assert data["total"] == 5
        assert data["has_next"] is True

    def test_page_size_is_capped(self, client, church):
        church.api_key = "chave-teto"
        church.save()
        for i in range(3):
            Person.objects.create(church=church, full_name=f"Pessoa {i}")

        response = client.get("/api/pessoas/?page_size=9999", HTTP_AUTHORIZATION="Bearer chave-teto")
        assert response.json()["page_size"] == 100


@pytest.mark.django_db
class TestApiEndpoints:
    def test_transactions_only_expose_income(self, client, church):
        from finance.models import Transaction

        church.api_key = "chave-financeiro"
        church.save()
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date="2026-03-01")
        Transaction.objects.create(church=church, type=Transaction.Type.EXPENSE, category=Transaction.Category.RENT, amount=50, date="2026-03-01")

        response = client.get("/api/doacoes/", HTTP_AUTHORIZATION="Bearer chave-financeiro")
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["category"] == "TITHE"


@pytest.mark.django_db
class TestApiWritePerson:
    def test_creates_person(self, client, church):
        church.api_key = "chave-escrita"
        church.save()

        response = client.post(
            "/api/pessoas/",
            data=json.dumps({"full_name": "Visitante via API", "phone": "62999990000", "role": "VISITOR", "status": "VISITOR"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer chave-escrita",
        )
        assert response.status_code == 201
        person = Person.objects.get()
        assert person.full_name == "Visitante via API"
        assert person.church_id == church.pk
        assert response.json()["id"] == person.pk

    def test_creates_audit_log_entry(self, client, church):
        from core.models import AuditLog

        church.api_key = "chave-audit"
        church.save()
        client.post(
            "/api/pessoas/",
            data=json.dumps({"full_name": "Auditada", "role": "VISITOR", "status": "VISITOR"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer chave-audit",
        )
        log = AuditLog.objects.get(model_name="Person")
        assert log.action == "CREATE"
        assert log.user is None  # API não loga usuário Django, só valida por chave

    def test_invalid_payload_returns_400_with_errors(self, client, church):
        church.api_key = "chave-invalida"
        church.save()

        response = client.post(
            "/api/pessoas/", data=json.dumps({}), content_type="application/json",
            HTTP_AUTHORIZATION="Bearer chave-invalida",
        )
        assert response.status_code == 400
        assert "full_name" in response.json()["errors"]

    def test_malformed_json_returns_400(self, client, church):
        church.api_key = "chave-malformada"
        church.save()

        response = client.post(
            "/api/pessoas/", data="{not json", content_type="application/json",
            HTTP_AUTHORIZATION="Bearer chave-malformada",
        )
        assert response.status_code == 400

    def test_respects_plan_person_limit(self, client, church):
        from unittest.mock import patch

        church.api_key = "chave-limite"
        church.save()

        with patch("api.views.pode_adicionar_pessoa", return_value=False):
            response = client.post(
                "/api/pessoas/",
                data=json.dumps({"full_name": "Além do limite", "role": "VISITOR", "status": "VISITOR"}),
                content_type="application/json",
                HTTP_AUTHORIZATION="Bearer chave-limite",
            )
        assert response.status_code == 403
        assert not Person.objects.exists()

    def test_patch_updates_only_sent_fields(self, client, church):
        church.api_key = "chave-patch"
        church.save()
        person = Person.objects.create(church=church, full_name="Original", phone="62911110000")

        response = client.patch(
            f"/api/pessoas/{person.pk}/", data=json.dumps({"phone": "62922220000"}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-patch",
        )
        assert response.status_code == 200
        person.refresh_from_db()
        assert person.phone == "62922220000"
        assert person.full_name == "Original"  # não mudou, não foi enviado

    def test_get_detail_returns_person(self, client, church):
        church.api_key = "chave-detalhe"
        church.save()
        person = Person.objects.create(church=church, full_name="Detalhe")

        response = client.get(f"/api/pessoas/{person.pk}/", HTTP_AUTHORIZATION="Bearer chave-detalhe")
        assert response.status_code == 200
        assert response.json()["full_name"] == "Detalhe"

    def test_cannot_access_person_from_another_church(self, client, church, outra_church):
        church.api_key = "chave-isolada"
        church.save()
        alheia = Person.objects.create(church=outra_church, full_name="De Outra Igreja")

        response = client.get(f"/api/pessoas/{alheia.pk}/", HTTP_AUTHORIZATION="Bearer chave-isolada")
        assert response.status_code == 404


@pytest.mark.django_db
class TestApiWriteTransaction:
    def test_creates_income_transaction(self, client, church):
        from finance.models import Transaction

        church.api_key = "chave-transacao"
        church.save()
        response = client.post(
            "/api/doacoes/",
            data=json.dumps({
                "type": "INCOME", "category": "DONATION", "amount": "150.00", "date": "2026-03-01",
            }),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-transacao",
        )
        assert response.status_code == 201
        transaction = Transaction.objects.get()
        assert transaction.amount == 150
        assert transaction.church_id == church.pk

    def test_creates_expense_transaction(self, client, church):
        from finance.models import Transaction

        church.api_key = "chave-despesa"
        church.save()
        response = client.post(
            "/api/doacoes/",
            data=json.dumps({
                "type": "EXPENSE", "category": "RENT", "amount": "80.00", "date": "2026-03-01",
            }),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-despesa",
        )
        assert response.status_code == 201
        assert Transaction.objects.get().type == "EXPENSE"

    def test_invalid_transaction_returns_400(self, client, church):
        church.api_key = "chave-transacao-invalida"
        church.save()
        response = client.post(
            "/api/doacoes/", data=json.dumps({}), content_type="application/json",
            HTTP_AUTHORIZATION="Bearer chave-transacao-invalida",
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestApiWriteRegistration:
    def _event(self, church, **kwargs):
        from events.models import Event

        defaults = {"church": church, "title": "Culto", "status": Event.EventStatus.PUBLISHED}
        from django.utils import timezone
        from datetime import timedelta

        defaults["start_datetime"] = timezone.now() + timedelta(days=3)
        defaults.update(kwargs)
        return Event.objects.create(**defaults)

    def test_creates_free_registration(self, client, church):
        from events.models import Registration

        church.api_key = "chave-inscricao"
        church.save()
        event = self._event(church)
        response = client.post(
            "/api/inscricoes/",
            data=json.dumps({
                "event_id": event.pk, "full_name": "Fulano", "phone": "62999998888",
                "email": "fulano@example.com", "consent": True,
            }),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-inscricao",
        )
        assert response.status_code == 201
        registration = Registration.objects.get()
        assert registration.payment_status == Registration.PaymentStatus.FREE
        assert registration.privacy_consent_at is not None

    def test_paid_event_gets_pending_status(self, client, church):
        from events.models import Registration

        church.api_key = "chave-inscricao-paga"
        church.save()
        event = self._event(church, is_paid=True, price=50)
        client.post(
            "/api/inscricoes/",
            data=json.dumps({
                "event_id": event.pk, "full_name": "Ciclana", "phone": "62999997777", "consent": True,
            }),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-inscricao-paga",
        )
        assert Registration.objects.get().payment_status == Registration.PaymentStatus.PENDING

    def test_full_event_registration_goes_to_waitlist(self, client, church):
        from events.models import Registration

        church.api_key = "chave-inscricao-lotado"
        church.save()
        event = self._event(church, capacity=1)
        Registration.objects.create(church=church, event=event, full_name="Já inscrito")
        response = client.post(
            "/api/inscricoes/",
            data=json.dumps({
                "event_id": event.pk, "full_name": "Fila de espera", "phone": "62999996666", "consent": True,
            }),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-inscricao-lotado",
        )
        assert response.status_code == 201
        assert response.json()["on_waitlist"] is True

    def test_without_consent_is_rejected(self, client, church):
        from events.models import Registration

        church.api_key = "chave-sem-consentimento"
        church.save()
        event = self._event(church)
        response = client.post(
            "/api/inscricoes/",
            data=json.dumps({"event_id": event.pk, "full_name": "Sem consentimento", "phone": "62999995555"}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-sem-consentimento",
        )
        assert response.status_code == 400
        assert not Registration.objects.exists()

    def test_invalid_event_id_returns_400(self, client, church):
        church.api_key = "chave-evento-invalido"
        church.save()
        response = client.post(
            "/api/inscricoes/",
            data=json.dumps({"event_id": 999999, "full_name": "X", "consent": True}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-evento-invalido",
        )
        assert response.status_code == 400

    def test_cannot_register_for_event_from_another_church(self, client, church, outra_church):
        church.api_key = "chave-evento-outra-igreja"
        church.save()
        event = self._event(outra_church)
        response = client.post(
            "/api/inscricoes/",
            data=json.dumps({"event_id": event.pk, "full_name": "X", "consent": True}),
            content_type="application/json", HTTP_AUTHORIZATION="Bearer chave-evento-outra-igreja",
        )
        assert response.status_code == 400
