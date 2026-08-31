"""Fase 3 — LGPD: consentimento nos 3 formulários públicos, política de
privacidade, autoatendimento (baixar dados / solicitar exclusão) no
Portal, fila de exclusão pra secretaria. Ver plano em
`.claude/plans/quiet-enchanting-seahorse.md`."""

import json

import pytest
from django.utils import timezone

from core.models import DataDeletionRequest
from custom_forms.models import CustomForm, FormField, FormResponse
from events.models import Event, Registration
from people.models import Person


@pytest.mark.django_db
class TestPrivacyPolicyPage:
    def test_page_loads_without_login(self, client):
        response = client.get("/privacidade/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestConsentRequiredOnPublicForms:
    def test_visitor_signup_without_consent_is_rejected(self, client, church):
        response = client.post(f"/{church.slug}/pessoas/cadastro/", {
            "full_name": "Sem Consentimento", "phone": "62911110000",
        })
        assert response.status_code == 200
        assert not Person.objects.filter(full_name="Sem Consentimento").exists()

    def test_visitor_signup_with_consent_records_timestamp(self, client, church):
        response = client.post(f"/{church.slug}/pessoas/cadastro/", {
            "full_name": "Com Consentimento", "phone": "62911110001", "privacy_consent": "on",
        })
        assert response.status_code == 302
        person = Person.objects.get(full_name="Com Consentimento")
        assert person.privacy_consent_at is not None

    def test_event_registration_without_consent_is_rejected(self, client, church):
        event = Event.objects.create(
            church=church, title="Culto", start_datetime=timezone.now(),
            status=Event.EventStatus.PUBLISHED,
        )
        response = client.post(f"/{church.slug}/eventos/{event.slug}/inscricao/", {
            "full_name": "Sem Consentimento", "phone": "62911110002", "email": "",
        })
        assert response.status_code == 200
        assert not Registration.objects.filter(full_name="Sem Consentimento").exists()

    def test_custom_form_without_consent_is_rejected(self, client, church):
        custom_form = CustomForm.objects.create(church=church, title="Pesquisa")
        field = FormField.objects.create(
            church=church, form=custom_form, label="Nome", field_type=FormField.FieldType.TEXT,
        )
        response = client.post(f"/{church.slug}/formularios/{custom_form.slug}/", {
            f"field_{field.pk}": "Alguém",
        })
        assert response.status_code == 200
        assert not FormResponse.objects.exists()


@pytest.mark.django_db
class TestMeusDados:
    def test_requires_login(self, client):
        response = client.get("/meus-dados/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_export_returns_own_data_as_json(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.get("/meus-dados/baixar/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"
        data = json.loads(response.content)
        assert data["nome_completo"] == person.full_name

    def test_export_without_linked_person_is_404(self, member_client):
        response = member_client.get("/meus-dados/baixar/")
        assert response.status_code == 404

    def test_request_deletion_creates_pending_request(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.post("/meus-dados/solicitar-exclusao/")
        assert response.status_code == 302
        assert DataDeletionRequest.objects.filter(
            person=person, status=DataDeletionRequest.Status.PENDING
        ).exists()

    def test_cannot_duplicate_pending_deletion_request(self, member_client, member_user, person, church):
        member_user.person = person
        member_user.save()
        DataDeletionRequest.objects.create(church=church, person=person, person_name=person.full_name)
        member_client.post("/meus-dados/solicitar-exclusao/")
        assert DataDeletionRequest.objects.filter(person=person).count() == 1


@pytest.mark.django_db
class TestDataDeletionRequestProcessing:
    def test_staff_sees_pending_requests(self, pastor_client, person, church):
        DataDeletionRequest.objects.create(church=church, person=person, person_name=person.full_name)
        response = pastor_client.get("/privacidade/solicitacoes/")
        assert response.status_code == 200
        assert person.full_name.encode() in response.content

    def test_member_cannot_see_staff_screen(self, member_client):
        response = member_client.get("/privacidade/solicitacoes/")
        assert response.status_code == 403

    def test_confirming_deletes_the_person_and_marks_request_done(self, pastor_client, pastor_user, person, church):
        deletion_request = DataDeletionRequest.objects.create(
            church=church, person=person, person_name=person.full_name
        )
        response = pastor_client.post(f"/privacidade/solicitacoes/{deletion_request.pk}/confirmar/")
        assert response.status_code == 302

        assert not Person.objects.filter(pk=person.pk).exists()
        deletion_request.refresh_from_db()
        assert deletion_request.status == DataDeletionRequest.Status.DONE
        assert deletion_request.processed_by == pastor_user
        # o snapshot do nome sobrevive à exclusão da Person (FK vira null)
        assert deletion_request.person_name == person.full_name
        assert deletion_request.person is None
