import pytest

from checkin.models import Checkin, SalaInfantil


@pytest.mark.django_db
class TestCheckinCreate:
    def test_create_with_registered_child_redirects_to_etiqueta(self, pastor_client, person, church):
        sala = SalaInfantil.objects.create(church=church, name="Berçário")
        response = pastor_client.post("/checkin/novo/", {
            "child": person.pk,
            "child_name": "",
            "sala": sala.pk,
            "guardian_name": "Responsável Teste",
            "guardian_phone": "62999990000",
        })
        assert response.status_code == 302
        checkin = Checkin.objects.get()
        assert response.url == f"/checkin/{checkin.pk}/etiqueta/"
        assert checkin.child_name == person.full_name
        assert checkin.child_id == person.pk

    def test_create_with_freetext_child_name_when_not_registered(self, pastor_client, church):
        response = pastor_client.post("/checkin/novo/", {
            "child": "",
            "child_name": "Criança Avulsa",
            "sala": "",
            "guardian_name": "Fulano",
            "guardian_phone": "",
        })
        assert response.status_code == 302
        checkin = Checkin.objects.get()
        assert checkin.child_name == "Criança Avulsa"
        assert checkin.child_id is None

    def test_requires_child_or_child_name(self, pastor_client):
        response = pastor_client.post("/checkin/novo/", {
            "child": "", "child_name": "", "sala": "",
            "guardian_name": "Fulano", "guardian_phone": "",
        })
        assert response.status_code == 200
        assert Checkin.objects.count() == 0

    def test_generates_unique_4_digit_pickup_code(self, pastor_client, church):
        pastor_client.post("/checkin/novo/", {
            "child": "", "child_name": "Criança 1", "sala": "",
            "guardian_name": "Fulano", "guardian_phone": "",
        })
        checkin = Checkin.objects.get()
        assert len(checkin.pickup_code) == 4
        assert checkin.pickup_code.isdigit()

    def test_member_cannot_create_checkin(self, member_client):
        response = member_client.get("/checkin/novo/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestCheckinCheckout:
    def test_checkout_marks_checked_out_at_and_by(self, pastor_client, pastor_user, church):
        checkin = Checkin.objects.create(church=church, child_name="Criança", guardian_name="Fulano")
        assert checkin.is_active is True

        response = pastor_client.post(f"/checkin/{checkin.pk}/checkout/")
        assert response.status_code == 302
        checkin.refresh_from_db()
        assert checkin.is_active is False
        assert checkin.checked_out_by == pastor_user

    def test_cannot_checkout_twice(self, pastor_client, church):
        checkin = Checkin.objects.create(church=church, child_name="Criança", guardian_name="Fulano")
        pastor_client.post(f"/checkin/{checkin.pk}/checkout/")
        response = pastor_client.post(f"/checkin/{checkin.pk}/checkout/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestCheckinBuscar:
    def test_finds_active_checkin_by_pickup_code(self, pastor_client, church):
        checkin = Checkin.objects.create(church=church, child_name="Criança", guardian_name="Fulano")
        response = pastor_client.get(f"/checkin/buscar/?code={checkin.pickup_code}")
        assert response.status_code == 200
        assert response.context["checkin"] == checkin

    def test_does_not_find_already_checked_out_code(self, pastor_client, church):
        from django.utils import timezone
        checkin = Checkin.objects.create(
            church=church, child_name="Criança", guardian_name="Fulano", checked_out_at=timezone.now(),
        )
        response = pastor_client.get(f"/checkin/buscar/?code={checkin.pickup_code}")
        assert response.context["checkin"] is None


@pytest.mark.django_db
class TestSalaInfantilCombinaComIdade:
    def test_matches_when_within_range(self, church):
        sala = SalaInfantil.objects.create(church=church, name="Berçário", idade_min=0, idade_max=2)
        assert sala.combina_com_idade(1) is True
        assert sala.combina_com_idade(5) is False

    def test_matches_any_age_when_no_range_set(self, church):
        sala = SalaInfantil.objects.create(church=church, name="Geral")
        assert sala.combina_com_idade(0) is True
        assert sala.combina_com_idade(99) is True
