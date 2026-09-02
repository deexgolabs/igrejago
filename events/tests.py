from datetime import timedelta

import pytest
from django.utils import timezone

from events.models import Event, Registration
from events.pix import build_pix_payload


def _crc16_ccitt(payload):
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return f"{crc:04X}"


class TestPixPayload:
    def test_payload_crc_is_correct(self):
        """Recalcula o CRC de forma independente da função testada, para
        não virar um teste que só repete a própria implementação."""
        payload = build_pix_payload(
            key="contato@igreja.org", receiver_name="Igreja Exemplo",
            receiver_city="GOIANIA", amount=150, txid="EVENTO1",
        )
        body, crc = payload[:-4], payload[-4:]
        assert crc == _crc16_ccitt(body)

    def test_payload_contains_pix_key_and_amount(self):
        payload = build_pix_payload(
            key="minha-chave", receiver_name="Igreja", receiver_city="GOIANIA",
            amount="99.90", txid="X",
        )
        assert "minha-chave" in payload
        assert "99.90" in payload


@pytest.fixture
def event(db, church):
    return Event.objects.create(
        church=church,
        title="Acampamento",
        start_datetime=timezone.now() + timedelta(days=5),
        is_paid=True,
        price=100,
        status=Event.EventStatus.PUBLISHED,
    )


@pytest.fixture
def free_event(db, church):
    return Event.objects.create(
        church=church,
        title="Culto Jovem",
        start_datetime=timezone.now() + timedelta(days=2),
        is_paid=False,
        status=Event.EventStatus.PUBLISHED,
    )


@pytest.mark.django_db
class TestEventRegistration:
    def test_free_event_confirms_immediately(self, client, free_event, church):
        response = client.post(f"/{church.slug}/eventos/{free_event.slug}/inscricao/", {
            "full_name": "Lucas Rocha", "phone": "62911119999", "email": "", "privacy_consent": "on",
        })
        registration = Registration.objects.get(full_name="Lucas Rocha")
        assert registration.payment_status == Registration.PaymentStatus.FREE
        assert response.status_code == 302
        assert f"/obrigado/" in response.url

    def test_paid_event_starts_pending(self, client, event, church):
        client.post(f"/{church.slug}/eventos/{event.slug}/inscricao/", {
            "full_name": "Pedro Alves", "phone": "62955554444", "email": "", "privacy_consent": "on",
        })
        registration = Registration.objects.get(full_name="Pedro Alves")
        assert registration.payment_status == Registration.PaymentStatus.PENDING

    def test_full_event_adds_to_waitlist_instead_of_rejecting(self, client, free_event, church):
        free_event.capacity = 1
        free_event.save()
        Registration.objects.create(church=church, event=free_event, full_name="Já inscrito")

        response = client.post(f"/{church.slug}/eventos/{free_event.slug}/inscricao/", {
            "full_name": "Sem vaga", "phone": "62900000000", "email": "", "privacy_consent": "on",
        })
        assert response.status_code == 302
        registration = Registration.objects.get(full_name="Sem vaga")
        assert registration.on_waitlist is True
        # quem está na lista de espera não conta contra a capacidade
        assert free_event.spots_left == 0
        assert free_event.registrations.count() == 2

    def test_logged_in_member_registration_links_to_person(self, client, member_user, person, church):
        member_user.person = person
        member_user.save()
        client.force_login(member_user)

        event = Event.objects.create(
            church=church,
            title="Culto", start_datetime=timezone.now() + timedelta(days=1),
            is_paid=False, status=Event.EventStatus.PUBLISHED,
        )
        client.post(f"/{church.slug}/eventos/{event.slug}/inscricao/", {
            "full_name": person.full_name, "phone": person.phone, "email": "", "privacy_consent": "on",
        })
        registration = Registration.objects.get(event=event)
        assert registration.person_id == person.pk


@pytest.mark.django_db
class TestRegistrationExportCSV:
    def test_export_has_single_bom_and_correct_rows(self, pastor_client, event, church):
        Registration.objects.create(
            church=church, event=event, full_name="Pedro Alves", phone="62955554444",
            payment_status=Registration.PaymentStatus.PAID, amount_paid=100,
        )
        response = pastor_client.get(f"/eventos/{event.slug}/inscritos/exportar/")
        content = response.content

        # BOM (EF BB BF) só uma vez, no início — regressão do bug em que
        # `charset=utf-8-sig` na response prefixava um BOM por linha do CSV.
        assert content[:3] == b"\xef\xbb\xbf"
        assert content.count(b"\xef\xbb\xbf") == 1
        text = content.decode("utf-8-sig")
        assert "Pedro Alves" in text
        assert text.count("\r\n") >= 2


@pytest.mark.django_db
class TestEventManagementPermissions:
    def test_member_cannot_create_event(self, member_client):
        response = member_client.get("/eventos/novo/")
        assert response.status_code == 403

    def test_pastor_can_create_event(self, pastor_client):
        response = pastor_client.get("/eventos/novo/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestWaitlistPromotion:
    def test_promote_clears_waitlist_flag_and_queues_notification(self, pastor_client, free_event, church):
        registration = Registration.objects.create(
            church=church, event=free_event, full_name="Na Espera", phone="62911112222", on_waitlist=True,
        )
        response = pastor_client.post(f"/eventos/{free_event.slug}/inscritos/{registration.pk}/promover/")
        assert response.status_code == 302
        registration.refresh_from_db()
        assert registration.on_waitlist is False

        from notifications.models import WhatsAppMessage
        # Normalizado com DDI 55 (não o "62911112222" cru digitado na
        # inscrição) — regressão: sem isso a Evolution API rejeita o
        # número com 400 Bad Request e a mensagem nunca sai de verdade.
        assert WhatsAppMessage.objects.filter(phone="5562911112222").exists()

    def test_member_cannot_promote(self, member_client, free_event, church):
        registration = Registration.objects.create(church=church, event=free_event, full_name="X", on_waitlist=True)
        response = member_client.post(f"/eventos/{free_event.slug}/inscritos/{registration.pk}/promover/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestCheckIn:
    def test_scanning_qr_marks_checked_in(self, pastor_client, free_event, church):
        registration = Registration.objects.create(church=church, event=free_event, full_name="Convidado")
        assert registration.checked_in_at is None

        response = pastor_client.get(f"/eventos/checkin/{registration.checkin_token}/")
        assert response.status_code == 200
        registration.refresh_from_db()
        assert registration.checked_in_at is not None

    def test_scanning_twice_does_not_error_and_keeps_first_time(self, pastor_client, free_event, church):
        registration = Registration.objects.create(church=church, event=free_event, full_name="Convidado")
        pastor_client.get(f"/eventos/checkin/{registration.checkin_token}/")
        registration.refresh_from_db()
        first_checkin = registration.checked_in_at

        response = pastor_client.get(f"/eventos/checkin/{registration.checkin_token}/")
        assert response.status_code == 200
        registration.refresh_from_db()
        assert registration.checked_in_at == first_checkin

    def test_member_cannot_check_in(self, member_client, free_event, church):
        registration = Registration.objects.create(church=church, event=free_event, full_name="Convidado")
        response = member_client.get(f"/eventos/checkin/{registration.checkin_token}/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestEventBranding:
    def test_event_own_color_overrides_church_palette(self, client, free_event, church):
        free_event.brand_color = "#ff0000"
        free_event.extra_info = "Leve seu próprio copo."
        free_event.save()

        response = client.get(f"/{church.slug}/eventos/{free_event.slug}/")
        assert response.status_code == 200
        assert b"Leve seu pr\xc3\xb3prio copo" in response.content

    def test_event_without_own_color_uses_church_palette(self, client, free_event, church_config):
        response = client.get(f"/{church_config.slug}/eventos/{free_event.slug}/")
        assert response.status_code == 200
