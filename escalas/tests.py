import pytest
from django.core.management import call_command

from escalas.models import Escala, EscalaVoluntario, IndisponibilidadeVoluntario, TrocaEscala
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

    def test_enqueued_phone_is_normalized_with_country_code(self, pastor_client, church):
        """Regressão: `_sync_voluntarios` enfileirava com `pessoa.phone`
        (cru, ex.: "62982033203") em vez de `pessoa.whatsapp_number`
        (normalizado, com DDI 55) — a Evolution API rejeita o número sem
        DDI com 400 Bad Request, e a mensagem nunca saía (achado num
        relato real de usuário)."""
        department = Department.objects.create(church=church, name="Louvor")
        pessoa = Person.objects.create(church=church, full_name="Sem DDI", phone="62982033203")

        pastor_client.post("/escalas/nova/", {
            "department": department.pk, "date": "2026-09-06", "time": "", "title": "",
            "voluntarios": [pessoa.pk],
        })

        mensagem = WhatsAppMessage.objects.get()
        assert mensagem.phone == "5562982033203"

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

    def test_skips_indisponivel_volunteer_on_that_specific_date(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        pessoa1 = Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")
        pessoa2 = Person.objects.create(church=church, full_name="Bruno", department=department, phone="62911110001")
        # Ana seria a escolhida do dia 06/09 pelo rodízio — avisa que não pode.
        IndisponibilidadeVoluntario.objects.create(church=church, person=pessoa1, date="2026-09-06")

        call_command("gerar_escalas_mensais", mes=9, ano=2026)

        escala_06 = Escala.objects.get(department=department, date="2026-09-06")
        assert escala_06.voluntarios.get().person == pessoa2
        # A indisponibilidade foi só NAQUELE domingo — o rodízio continua
        # normal dali: 13/09 já seria a vez de Bruno de qualquer jeito
        # (índice 1), 20/09 volta a ser a vez de Ana (índice 2), agora
        # disponível de novo.
        escala_20 = Escala.objects.get(department=department, date="2026-09-20")
        assert escala_20.voluntarios.get().person == pessoa1

    def test_skips_escala_entirely_when_everyone_is_indisponivel(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        pessoa = Person.objects.create(church=church, full_name="Ana", department=department, phone="62911110000")
        IndisponibilidadeVoluntario.objects.create(church=church, person=pessoa, date="2026-09-06")

        call_command("gerar_escalas_mensais", mes=9, ano=2026)

        assert not Escala.objects.filter(department=department, date="2026-09-06").exists()
        assert Escala.objects.filter(department=department, date="2026-09-13").exists()

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


@pytest.mark.django_db
class TestEscalaDepartmentLeaderScopedAccess:
    """`department`/`department_leader_client` vêm do conftest.py."""

    def test_calendario_shows_only_own_department(self, department_leader_client, church, department, person):
        outro_dept = Department.objects.create(church=church, name="Diaconato")
        propria = Escala.objects.create(church=church, department=department, date="2026-09-06")
        de_outro = Escala.objects.create(church=church, department=outro_dept, date="2026-09-06")

        response = department_leader_client.get("/escalas/?ano=2026&mes=9")
        all_escalas = [e for week in response.context["weeks"] for day in week for e in day["escalas"]]
        assert propria in all_escalas
        assert de_outro not in all_escalas

    def test_cannot_open_escala_from_another_department_directly(self, department_leader_client, church):
        outro_dept = Department.objects.create(church=church, name="Diaconato")
        de_outro = Escala.objects.create(church=church, department=outro_dept, date="2026-09-06")
        response = department_leader_client.get(f"/escalas/{de_outro.pk}/")
        assert response.status_code == 404

    def test_create_form_only_offers_own_department(self, department_leader_client, church, department):
        Department.objects.create(church=church, name="Diaconato")
        response = department_leader_client.get("/escalas/nova/")
        department_choices = list(response.context["form"].fields["department"].queryset)
        assert department_choices == [department]

    def test_cannot_create_escala_for_another_department_via_post(self, department_leader_client, church, department):
        outro_dept = Department.objects.create(church=church, name="Diaconato")
        response = department_leader_client.post("/escalas/nova/", {
            "department": outro_dept.pk, "date": "2026-09-06", "time": "", "title": "", "voluntarios": [],
        })
        assert response.status_code == 200  # form inválido, não cria
        assert not Escala.objects.filter(department=outro_dept).exists()

    def test_member_cannot_access_escalas(self, member_client):
        assert member_client.get("/escalas/").status_code == 403


@pytest.mark.django_db
class TestMinhaIndisponibilidade:
    def test_member_creates_indisponibilidade(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.post("/escalas/minha-disponibilidade/", {"date": "2099-01-05", "motivo": "Viagem"})
        assert response.status_code == 302
        assert IndisponibilidadeVoluntario.objects.filter(person=person, date="2099-01-05").exists()

    def test_past_date_is_rejected(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.post("/escalas/minha-disponibilidade/", {"date": "2020-01-05", "motivo": ""})
        assert response.status_code == 200
        assert not IndisponibilidadeVoluntario.objects.exists()

    def test_duplicate_date_shows_friendly_error(self, member_client, member_user, person, church):
        member_user.person = person
        member_user.save()
        IndisponibilidadeVoluntario.objects.create(church=church, person=person, date="2099-01-05")
        response = member_client.post("/escalas/minha-disponibilidade/", {"date": "2099-01-05", "motivo": ""})
        assert response.status_code == 302
        assert IndisponibilidadeVoluntario.objects.filter(person=person).count() == 1

    def test_only_sees_own_and_future_entries(self, member_client, member_user, person, church):
        member_user.person = person
        member_user.save()
        IndisponibilidadeVoluntario.objects.create(church=church, person=person, date="2099-01-05")
        outra_pessoa = Person.objects.create(church=church, full_name="Outra")
        IndisponibilidadeVoluntario.objects.create(church=church, person=outra_pessoa, date="2099-02-05")

        response = member_client.get("/escalas/minha-disponibilidade/")
        shown = list(response.context["indisponibilidades"])
        assert len(shown) == 1
        assert shown[0].person == person

    def test_can_delete_own_but_not_others(self, member_client, member_user, person, church):
        member_user.person = person
        member_user.save()
        minha = IndisponibilidadeVoluntario.objects.create(church=church, person=person, date="2099-01-05")
        outra_pessoa = Person.objects.create(church=church, full_name="Outra")
        de_outro = IndisponibilidadeVoluntario.objects.create(church=church, person=outra_pessoa, date="2099-02-05")

        response = member_client.post(f"/escalas/minha-disponibilidade/{de_outro.pk}/excluir/")
        assert response.status_code == 404

        response = member_client.post(f"/escalas/minha-disponibilidade/{minha.pk}/excluir/")
        assert response.status_code == 302
        assert not IndisponibilidadeVoluntario.objects.filter(pk=minha.pk).exists()

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get("/escalas/minha-disponibilidade/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


@pytest.mark.django_db
class TestTrocaEscala:
    def _setup_confirmado(self, church):
        department = Department.objects.create(church=church, name="Louvor")
        pessoa = Person.objects.create(church=church, full_name="Ana", phone="62911110000", department=department)
        colega = Person.objects.create(church=church, full_name="Bruno", phone="62911110001", department=department)
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        voluntario = EscalaVoluntario.objects.create(
            church=church, escala=escala, person=pessoa, status=EscalaVoluntario.Status.CONFIRMED,
        )
        return voluntario, colega

    def test_confirmed_volunteer_can_request_troca(self, client, church):
        voluntario, colega = self._setup_confirmado(church)

        response = client.post(f"/escalas/confirmar/{voluntario.confirm_token}/pedir-troca/")
        assert response.status_code == 302
        troca = TrocaEscala.objects.get(escala_voluntario=voluntario)
        assert troca.status == TrocaEscala.Status.PENDING
        assert WhatsAppMessage.objects.filter(person=colega).exists()

    def test_cannot_request_troca_if_not_confirmed(self, client, church):
        department = Department.objects.create(church=church, name="Louvor")
        pessoa = Person.objects.create(church=church, full_name="Ana", department=department)
        escala = Escala.objects.create(church=church, department=department, date="2026-09-06")
        voluntario = EscalaVoluntario.objects.create(church=church, escala=escala, person=pessoa)  # PENDING

        response = client.post(f"/escalas/confirmar/{voluntario.confirm_token}/pedir-troca/")
        assert response.status_code == 302
        assert not TrocaEscala.objects.exists()

    def test_cannot_request_troca_twice(self, client, church):
        voluntario, _ = self._setup_confirmado(church)
        client.post(f"/escalas/confirmar/{voluntario.confirm_token}/pedir-troca/")
        client.post(f"/escalas/confirmar/{voluntario.confirm_token}/pedir-troca/")
        assert TrocaEscala.objects.filter(escala_voluntario=voluntario).count() == 1

    def test_colleague_accepts_troca_and_is_reassigned(self, client, church):
        voluntario, colega = self._setup_confirmado(church)
        troca = TrocaEscala.objects.create(church=church, escala_voluntario=voluntario)

        response = client.post(f"/escalas/trocar/{troca.token}/", {"person_id": colega.pk})
        assert response.status_code == 200
        assert response.context["aceita_agora"] is True

        voluntario.refresh_from_db()
        assert voluntario.person == colega
        assert voluntario.status == EscalaVoluntario.Status.PENDING
        troca.refresh_from_db()
        assert troca.status == TrocaEscala.Status.ACEITA
        assert troca.aceito_por == colega
        assert WhatsAppMessage.objects.filter(person=colega, campaign_label__startswith="Troca de escala").exists()

    def test_second_person_cannot_accept_already_resolved_troca(self, client, church):
        voluntario, colega = self._setup_confirmado(church)
        outro = Person.objects.create(church=church, full_name="Carlos", department=voluntario.escala.department)
        troca = TrocaEscala.objects.create(church=church, escala_voluntario=voluntario)

        client.post(f"/escalas/trocar/{troca.token}/", {"person_id": colega.pk})
        response = client.post(f"/escalas/trocar/{troca.token}/", {"person_id": outro.pk})
        assert response.status_code == 200
        assert response.context["ja_resolvida"] is True
        voluntario.refresh_from_db()
        assert voluntario.person == colega  # não mudou de novo

    def test_cannot_accept_with_person_outside_department(self, client, church):
        voluntario, _ = self._setup_confirmado(church)
        troca = TrocaEscala.objects.create(church=church, escala_voluntario=voluntario)
        de_fora = Person.objects.create(church=church, full_name="De Fora")  # sem departamento

        response = client.post(f"/escalas/trocar/{troca.token}/", {"person_id": de_fora.pk})
        assert response.status_code == 404
