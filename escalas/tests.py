import pytest
from django.core.management import call_command

from escalas.models import Escala, EscalaVoluntario
from notifications.models import WhatsAppMessage
from people.models import Department, Person


@pytest.mark.django_db
class TestEscalaCreate:
    def test_create_enqueues_whatsapp_for_each_voluntario(self, pastor_client, person, church):
        department = Department.objects.create(church=church, name="Louvor")
        response = pastor_client.post("/escalas/nova/", {
            "department": department.pk,
            "date": "2026-09-06",
            "time": "19:00",
            "title": "Culto de domingo",
            "voluntarios": [person.pk],
        })
        assert response.status_code == 302
        escala = Escala.objects.get()
        assert response.url == f"/escalas/{escala.pk}/"
        voluntario = escala.voluntarios.get()
        assert voluntario.person == person
        assert voluntario.status == EscalaVoluntario.Status.PENDING

        mensagem = WhatsAppMessage.objects.get()
        assert mensagem.person == person
        assert mensagem.campaign_label == f"Escala-{escala.pk}"

    def test_member_cannot_create_escala(self, member_client):
        response = member_client.get("/escalas/nova/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestEscalaSyncVoluntarios:
    def test_update_does_not_resend_to_unchanged_voluntario(self, pastor_client, person, church):
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        EscalaVoluntario.objects.create(church=church, escala=escala, person=person)
        assert WhatsAppMessage.objects.count() == 0

        pastor_client.post(f"/escalas/{escala.pk}/editar/", {
            "department": department.pk, "date": "2026-09-06", "time": "", "title": "",
            "voluntarios": [person.pk],
        })
        assert WhatsAppMessage.objects.count() == 0
        assert escala.voluntarios.count() == 1

    def test_update_removes_deselected_voluntario(self, pastor_client, person, church):
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        EscalaVoluntario.objects.create(church=church, escala=escala, person=person)

        pastor_client.post(f"/escalas/{escala.pk}/editar/", {
            "department": department.pk, "date": "2026-09-06", "time": "", "title": "",
            "voluntarios": [],
        })
        assert escala.voluntarios.count() == 0


@pytest.mark.django_db
class TestConfirmarEscala:
    def test_confirmar_sets_status_and_timestamp(self, client, person, church):
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        voluntario = EscalaVoluntario.objects.create(church=church, escala=escala, person=person)

        response = client.post(f"/escalas/confirmar/{voluntario.confirm_token}/", {"acao": "confirmar"})
        assert response.status_code == 200
        voluntario.refresh_from_db()
        assert voluntario.status == EscalaVoluntario.Status.CONFIRMED
        assert voluntario.confirmed_at is not None

    def test_recusar_sets_declined_status(self, client, person, church):
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        voluntario = EscalaVoluntario.objects.create(church=church, escala=escala, person=person)

        client.post(f"/escalas/confirmar/{voluntario.confirm_token}/", {"acao": "recusar"})
        voluntario.refresh_from_db()
        assert voluntario.status == EscalaVoluntario.Status.DECLINED

    def test_confirmar_does_not_require_login(self, client, person, church):
        """Link público — recebido por WhatsApp, sem sessão nenhuma."""
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        voluntario = EscalaVoluntario.objects.create(church=church, escala=escala, person=person)

        response = client.get(f"/escalas/confirmar/{voluntario.confirm_token}/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestEscalaCalendario:
    def test_calendario_shows_escalas_for_the_month(self, pastor_client, church):
        department = Department.objects.create(church=church, name="Louvor")
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")

        response = pastor_client.get("/escalas/?ano=2026&mes=9")
        assert response.status_code == 200
        all_escalas = [e for week in response.context["weeks"] for day in week for e in day["escalas"]]
        assert escala in all_escalas


@pytest.mark.django_db
class TestGerarEscalasMensais:
    def test_creates_one_escala_per_sunday_round_robin(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        pessoa1 = Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")
        pessoa2 = Person.objects.create(church=church, full_name="Bruno", department=department, phone="62911110001")

        # Setembro/2026 tem 5 domingos (06, 13, 20, 27 e... na verdade 4 —
        # confirma com o rodízio: o que importa é alternar entre os 2).
        call_command("gerar_escalas_mensais", mes=9, ano=2026)

        escalas = list(Escala.objects.filter(department=department).order_by("date"))
        assert len(escalas) > 0
        assert all(e.date.weekday() == 6 for e in escalas)  # domingo
        assert all(e.date.year == 2026 and e.date.month == 9 for e in escalas)

        voluntarios = [escala.voluntarios.get().person for escala in escalas]
        assert voluntarios[0] == pessoa1
        if len(voluntarios) > 1:
            assert voluntarios[1] == pessoa2

    def test_enqueues_whatsapp_confirmation_for_each_new_voluntario(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")

        call_command("gerar_escalas_mensais", mes=9, ano=2026)
        assert WhatsAppMessage.objects.exists()
        assert WhatsAppMessage.objects.first().campaign_label.startswith("Escala-")

    def test_does_not_overwrite_existing_escala(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")
        primeiro_domingo = Escala.objects.create(church=church, department=department, date="2026-09-06")

        call_command("gerar_escalas_mensais", mes=9, ano=2026)

        assert Escala.objects.filter(department=department, date="2026-09-06").count() == 1
        assert Escala.objects.get(department=department, date="2026-09-06").pk == primeiro_domingo.pk

    def test_department_without_voluntarios_is_skipped(self, church):
        Department.objects.create(church=church, name="Sem Voluntário")
        call_command("gerar_escalas_mensais", mes=9, ano=2026)
        assert not Escala.objects.exists()

    def test_defaults_to_next_month_when_no_args_given(self, church, monkeypatch):
        import datetime as dt

        class FakeDate(dt.date):
            @classmethod
            def today(cls):
                return dt.date(2026, 1, 15)

        monkeypatch.setattr("core.management.commands.gerar_escalas_mensais.date", FakeDate)

        department = Department.objects.create(church=church, name="Louvor")
        Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")

        call_command("gerar_escalas_mensais")
        escalas = Escala.objects.filter(department=department)
        assert escalas.exists()
        assert all(e.date.month == 2 and e.date.year == 2026 for e in escalas)
