from datetime import date, timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from notifications.models import WhatsAppMessage
from people.models import AutomacaoJornada, Department, Family, Person, Tag


@pytest.mark.django_db
class TestCampaign:
    def test_queues_only_for_filtered_people_with_phone(self, pastor_client, person, church):
        """A campanha não envia nada na hora — só enfileira uma
        WhatsAppMessage por destinatário (ver [[project-church-crm]]:
        quem manda de verdade, com intervalo, é `processar_fila_whatsapp`)."""
        Person.objects.create(church=church, full_name="Sem Telefone", is_member=True, status=Person.Status.ACTIVE)
        other = Person.objects.create(
            church=church, full_name="Outro Cargo", phone="62911110000", is_member=True,
            status=Person.Status.ACTIVE, role=Person.Role.DEACON,
        )

        response = pastor_client.post(f"/pessoas/campanha/?role={Person.Role.MEMBER}", {
            "message": "Olá {nome}, culto hoje às 19h!",
        })
        assert response.status_code == 302

        queued = WhatsAppMessage.objects.all()
        assert queued.count() == 1
        assert queued.first().person == person
        assert queued.first().status == WhatsAppMessage.Status.PENDING
        assert "Olá Maria Souza" in queued.first().message
        assert not WhatsAppMessage.objects.filter(person=other).exists()

    def test_member_cannot_access_campaign(self, member_client):
        response = member_client.get("/pessoas/campanha/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestEmailCampaign:
    def test_queues_only_for_people_with_email(self, pastor_client, church):
        from notifications.models import EmailMessage

        com_email = Person.objects.create(church=church, full_name="Com E-mail", email="com@example.com")
        Person.objects.create(church=church, full_name="Sem E-mail")

        response = pastor_client.post("/pessoas/campanha/email/", {
            "subject": "Aviso importante", "message": "Olá {nome}, culto hoje às 19h!",
        })
        assert response.status_code == 302

        queued = EmailMessage.objects.all()
        assert queued.count() == 1
        assert queued.first().person == com_email
        assert "Olá Com E-mail" in queued.first().body

    def test_member_cannot_access_email_campaign(self, member_client):
        assert member_client.get("/pessoas/campanha/email/").status_code == 403


@pytest.mark.django_db
class TestSMSCampaign:
    def test_queues_only_for_people_with_phone(self, pastor_client, person, church):
        from notifications.models import SMSMessage

        Person.objects.create(church=church, full_name="Sem Telefone")

        response = pastor_client.post("/pessoas/campanha/sms/", {"message": "Olá {nome}!"})
        assert response.status_code == 302

        queued = SMSMessage.objects.all()
        assert queued.count() == 1
        assert queued.first().person == person
        assert queued.first().phone == "5562999998888"

    def test_member_cannot_access_sms_campaign(self, member_client):
        assert member_client.get("/pessoas/campanha/sms/").status_code == 403


@pytest.mark.django_db
class TestPersonModel:
    def test_age_computed_from_birth_date(self, person):
        person.birth_date = date(1990, 3, 15)
        person.save()
        expected = date.today().year - 1990
        if (date.today().month, date.today().day) < (3, 15):
            expected -= 1
        assert person.age == expected

    def test_age_is_none_without_birth_date(self, person):
        person.birth_date = None
        assert person.age is None

    @pytest.mark.parametrize("raw,expected", [
        ("62999998888", "5562999998888"),
        ("(62) 99999-8888", "5562999998888"),
        ("5562999998888", "5562999998888"),
        ("", ""),
    ])
    def test_whatsapp_number_normalizes_to_e164_with_ddi(self, person, raw, expected):
        person.phone = raw
        assert person.whatsapp_number == expected


@pytest.mark.django_db
class TestPersonPermissions:
    def test_member_gets_403_on_people_list(self, member_client):
        response = member_client.get("/pessoas/")
        assert response.status_code == 403

    def test_pastor_can_access_people_list(self, pastor_client, person):
        response = pastor_client.get("/pessoas/")
        assert response.status_code == 200
        assert person.full_name.encode() in response.content

    def test_anonymous_redirected_to_login(self, client):
        response = client.get("/pessoas/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


@pytest.mark.django_db
class TestPublicVisitorSignup:
    def test_signup_creates_visitor_without_login(self, client, church):
        response = client.post(f"/{church.slug}/pessoas/cadastro/", {
            "full_name": "Carla Mendes",
            "phone": "62933332222",
            "email": "",
            "birth_date": "",
            "wants_membership": "on",
            "privacy_consent": "on",
        })
        assert response.status_code == 302
        created = Person.objects.get(full_name="Carla Mendes")
        assert created.is_visitor is True
        assert created.status == Person.Status.VISITOR_ONLY
        assert created.wants_membership is True


@pytest.mark.django_db
class TestPersonCRUD:
    def test_pastor_can_create_person(self, pastor_client):
        response = pastor_client.post("/pessoas/novo/", {
            "full_name": "João Pedro",
            "phone": "62988887777",
            "email": "",
            "role": "MEMBER",
            "status": "ACTIVE",
            "is_member": "on",
        })
        assert response.status_code == 302
        assert Person.objects.filter(full_name="João Pedro").exists()

    def test_member_cannot_create_person(self, member_client):
        response = member_client.post("/pessoas/novo/", {"full_name": "X"})
        assert response.status_code == 403

    def test_pastor_can_delete_person(self, pastor_client, person):
        response = pastor_client.post(f"/pessoas/{person.pk}/excluir/")
        assert response.status_code == 302
        assert not Person.objects.filter(pk=person.pk).exists()


@pytest.mark.django_db
class TestPersonImportFlow:
    def test_review_then_confirm_creates_person_with_edits(self, pastor_client):
        """O passo de revisão deve permitir editar os dados antes de
        confirmar — aqui simulamos o POST de confirmação diretamente
        (o parse do Excel/CSV é testado à parte)."""
        formset_data = {
            "step": "review",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-include": "on",
            "form-0-full_name": "Nome Corrigido",
            "form-0-phone": "62911112222",
            "form-0-email": "",
            "form-0-birth_date": "",
            "form-0-role": "MEMBER",
            "form-0-status": "ACTIVE",
        }
        response = pastor_client.post("/pessoas/importar/", formset_data)
        assert response.status_code == 302
        created = Person.objects.get(full_name="Nome Corrigido")
        assert created.role == "MEMBER"
        assert created.is_member is True

    def test_excluded_row_is_not_created(self, pastor_client):
        formset_data = {
            "step": "review",
            "form-TOTAL_FORMS": "1",
            "form-INITIAL_FORMS": "1",
            "form-MIN_NUM_FORMS": "0",
            "form-MAX_NUM_FORMS": "1000",
            "form-0-full_name": "Nao Deveria Existir",
            "form-0-phone": "",
            "form-0-email": "",
            "form-0-birth_date": "",
            "form-0-role": "VISITOR",
            "form-0-status": "VISITOR",
            # sem "form-0-include" = desmarcado
        }
        pastor_client.post("/pessoas/importar/", formset_data)
        assert not Person.objects.filter(full_name="Nao Deveria Existir").exists()

    def test_duplicate_by_phone_is_flagged_and_unchecked(self, pastor_client, person):
        """`person` já existe com telefone 62999998888 (ver conftest.py)."""
        import io

        import openpyxl
        from django.core.files.uploadedfile import SimpleUploadedFile

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["nome", "telefone", "email", "data_nascimento"])
        ws.append(["Nome Diferente", "(62) 99999-8888", "", ""])
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        response = pastor_client.post("/pessoas/importar/", {
            "file": SimpleUploadedFile("planilha.xlsx", buffer.read()),
        })
        assert response.status_code == 200
        formset = response.context["formset"]
        assert formset.forms[0].initial["include"] is False

    def test_parse_spreadsheet_skips_blank_names(self):
        import io

        import openpyxl
        from django.core.files.uploadedfile import SimpleUploadedFile

        from people.views import _parse_spreadsheet

        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["nome", "telefone", "email", "data_nascimento"])
        ws.append(["Ana Silva", "62999990000", "", "15/03/1990"])
        ws.append(["", "62999991111", "", ""])
        buffer = io.BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        uploaded = SimpleUploadedFile("planilha.xlsx", buffer.read())
        rows = _parse_spreadsheet(uploaded)

        assert len(rows) == 1
        assert rows[0]["full_name"] == "Ana Silva"
        assert rows[0]["birth_date"] == "1990-03-15"


@pytest.mark.django_db
class TestPipelineBoard:
    def test_board_renders_with_default_column(self, pastor_client, person):
        response = pastor_client.get("/pessoas/acompanhamento/")
        assert response.status_code == 200
        assert person.full_name.encode() in response.content

    def test_move_updates_stage(self, pastor_client, person):
        response = pastor_client.post(
            f"/pessoas/acompanhamento/{person.pk}/mover/", {"stage": Person.PipelineStage.INTEGRATED}
        )
        assert response.status_code == 204
        person.refresh_from_db()
        assert person.pipeline_stage == Person.PipelineStage.INTEGRATED

    def test_move_rejects_invalid_stage(self, pastor_client, person):
        response = pastor_client.post(f"/pessoas/acompanhamento/{person.pk}/mover/", {"stage": "NAO_EXISTE"})
        assert response.status_code == 400

    def test_member_cannot_move(self, member_client, person):
        response = member_client.post(
            f"/pessoas/acompanhamento/{person.pk}/mover/", {"stage": Person.PipelineStage.INTEGRATED}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestFamily:
    def test_create_and_list(self, pastor_client):
        response = pastor_client.post("/pessoas/familias/", {"name": "Família Silva"})
        assert response.status_code == 302
        assert Family.objects.filter(name="Família Silva").exists()

    def test_detail_shows_members(self, pastor_client, person, church):
        family = Family.objects.create(church=church, name="Família Souza")
        person.family = family
        person.save()
        response = pastor_client.get(f"/pessoas/familias/{family.pk}/")
        assert person.full_name.encode() in response.content

    def test_delete_family_keeps_person(self, pastor_client, person, church):
        family = Family.objects.create(church=church, name="Família Temp")
        person.family = family
        person.save()
        response = pastor_client.post(f"/pessoas/familias/{family.pk}/excluir/")
        assert response.status_code == 302
        person.refresh_from_db()
        assert person.family is None


@pytest.mark.django_db
class TestTag:
    def test_create_and_list(self, pastor_client):
        response = pastor_client.post("/pessoas/tags/", {"name": "Louvor", "color": "#ff0000"})
        assert response.status_code == 302
        assert Tag.objects.filter(name="Louvor").exists()

    def test_delete_tag(self, pastor_client, person, church):
        tag = Tag.objects.create(church=church, name="Temp")
        person.tags.add(tag)
        response = pastor_client.post(f"/pessoas/tags/{tag.pk}/excluir/")
        assert response.status_code == 302
        assert not Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
class TestPipelineStageChangedAt:
    def test_set_on_creation(self, church):
        person = Person.objects.create(church=church, full_name="Novo Visitante")
        assert person.pipeline_stage_changed_at is not None

    def test_updated_when_stage_changes(self, church):
        person = Person.objects.create(church=church, full_name="Visitante")
        original = person.pipeline_stage_changed_at

        person.pipeline_stage = Person.PipelineStage.FOLLOWING_UP
        person.save()
        person.refresh_from_db()
        assert person.pipeline_stage_changed_at > original

    def test_not_touched_when_other_fields_change(self, church):
        person = Person.objects.create(church=church, full_name="Visitante")
        original = person.pipeline_stage_changed_at

        person.notes = "Ligou pra igreja"
        person.save()
        person.refresh_from_db()
        assert person.pipeline_stage_changed_at == original


@pytest.mark.django_db
class TestProcessarAutomacaoJornada:
    def test_queues_message_for_person_matching_stage_and_days(self, church):
        AutomacaoJornada.objects.create(
            church=church, etapa=Person.PipelineStage.NEW_VISITOR, dias_depois=3,
            mensagem="Oi {nome}, tudo bem?",
        )
        person = Person.objects.create(church=church, full_name="Maria", phone="62911112222")
        Person.objects.filter(pk=person.pk).update(
            pipeline_stage_changed_at=timezone.now() - timedelta(days=3)
        )

        call_command("processar_automacao_jornada")

        msg = WhatsAppMessage.objects.get()
        assert msg.person == person
        assert msg.message == "Oi Maria, tudo bem?"
        assert msg.campaign_label.startswith("Jornada-")

    def test_does_not_queue_when_days_dont_match(self, church):
        AutomacaoJornada.objects.create(
            church=church, etapa=Person.PipelineStage.NEW_VISITOR, dias_depois=3, mensagem="Oi {nome}",
        )
        person = Person.objects.create(church=church, full_name="Maria", phone="62911112222")
        Person.objects.filter(pk=person.pk).update(
            pipeline_stage_changed_at=timezone.now() - timedelta(days=1)
        )

        call_command("processar_automacao_jornada")
        assert not WhatsAppMessage.objects.exists()

    def test_running_twice_the_same_day_does_not_duplicate(self, church):
        AutomacaoJornada.objects.create(
            church=church, etapa=Person.PipelineStage.NEW_VISITOR, dias_depois=0, mensagem="Oi {nome}",
        )
        Person.objects.create(church=church, full_name="Maria", phone="62911112222")

        call_command("processar_automacao_jornada")
        call_command("processar_automacao_jornada")
        assert WhatsAppMessage.objects.count() == 1

    def test_inactive_rule_is_ignored(self, church):
        AutomacaoJornada.objects.create(
            church=church, etapa=Person.PipelineStage.NEW_VISITOR, dias_depois=0,
            mensagem="Oi {nome}", ativo=False,
        )
        Person.objects.create(church=church, full_name="Maria", phone="62911112222")

        call_command("processar_automacao_jornada")
        assert not WhatsAppMessage.objects.exists()

    def test_person_without_phone_is_not_queued(self, church):
        AutomacaoJornada.objects.create(
            church=church, etapa=Person.PipelineStage.NEW_VISITOR, dias_depois=0, mensagem="Oi {nome}",
        )
        Person.objects.create(church=church, full_name="Sem Telefone")

        call_command("processar_automacao_jornada")
        assert not WhatsAppMessage.objects.exists()


@pytest.mark.django_db
class TestDepartmentCRUD:
    def test_pastor_can_create_department(self, pastor_client):
        response = pastor_client.post("/pessoas/departamentos/novo/", {
            "name": "Infantil", "leader": "", "habilita_checkin": "on",
        })
        assert response.status_code == 302
        dept = Department.objects.get(name="Infantil")
        assert dept.habilita_checkin is True

    def test_department_leader_cannot_access_department_crud(self, department_leader_client):
        # Líder de Departamento é escopado ao próprio departamento — não
        # gerencia a estrutura de departamentos em si (isso é
        # `IsChurchManagerMixin`, só Pastor/Secretaria).
        assert department_leader_client.get("/pessoas/departamentos/").status_code == 403
        assert department_leader_client.get("/pessoas/departamentos/novo/").status_code == 403

    def test_member_cannot_access_department_crud(self, member_client):
        assert member_client.get("/pessoas/departamentos/").status_code == 403


@pytest.mark.django_db
class TestPersonUpdateRole:
    def test_pastor_promotes_person_to_leader(self, pastor_client, person, member_user):
        member_user.person = person
        member_user.save(update_fields=["person"])

        response = pastor_client.post(f"/pessoas/{person.pk}/atualizar-acesso/", {"role": "LEADER"})
        assert response.status_code == 302
        member_user.refresh_from_db()
        assert member_user.role == "LEADER"

    def test_department_leader_cannot_change_roles(self, department_leader_client, church, member_user):
        # `person` (fixture) já está vinculada ao `department_leader_user`
        # (ver `department_leader_client` no conftest.py) — usa uma pessoa
        # nova aqui, senão bateria na constraint de unicidade do O2O.
        outra_pessoa = Person.objects.create(church=church, full_name="Outra Pessoa")
        member_user.person = outra_pessoa
        member_user.save(update_fields=["person"])
        response = department_leader_client.post(f"/pessoas/{outra_pessoa.pk}/atualizar-acesso/", {"role": "PASTOR"})
        assert response.status_code == 403

    def test_errors_gracefully_when_person_has_no_account(self, pastor_client, person):
        response = pastor_client.post(f"/pessoas/{person.pk}/atualizar-acesso/", {"role": "LEADER"})
        assert response.status_code == 302
        assert not hasattr(person, "user_account")


@pytest.mark.django_db
class TestDepartmentLeaderScopedAccess:
    """`department`/`department_leader_client` vêm do conftest.py — um
    Líder de Departamento (role=LEADER) que lidera `department`."""

    def test_sees_only_own_department_people(self, department_leader_client, church, department, person):
        outro_dept = Department.objects.create(church=church, name="Diaconato")
        outra_pessoa = Person.objects.create(
            church=church, full_name="Fora do Departamento", department=outro_dept,
            is_member=True, status=Person.Status.ACTIVE,
        )
        person.department = department
        person.save(update_fields=["department"])

        response = department_leader_client.get("/pessoas/")
        names = [p.full_name for p in response.context["people"]]
        assert person.full_name in names
        assert outra_pessoa.full_name not in names

    def test_cannot_open_person_from_another_department_directly(self, department_leader_client, church):
        outro_dept = Department.objects.create(church=church, name="Diaconato")
        outra_pessoa = Person.objects.create(
            church=church, full_name="Fora do Departamento", department=outro_dept,
            is_member=True, status=Person.Status.ACTIVE,
        )
        response = department_leader_client.get(f"/pessoas/{outra_pessoa.pk}/editar/")
        assert response.status_code == 404

    def test_created_person_is_forced_into_own_department(self, department_leader_client, church, department):
        response = department_leader_client.post("/pessoas/novo/", {
            "full_name": "Novo Cadastro", "department": "",  # tenta deixar em branco
            "role": "MEMBER", "status": "ACTIVE", "pipeline_stage": "",
        })
        assert response.status_code == 302, response.context["form"].errors if response.status_code == 200 else None
        pessoa = Person.objects.get(full_name="Novo Cadastro")
        assert pessoa.department_id == department.pk

    def test_leader_role_without_a_department_sees_nobody(self, member_client, member_user, church):
        member_user.role = "LEADER"
        member_user.save(update_fields=["role"])
        Person.objects.create(church=church, full_name="Alguém", is_member=True, status=Person.Status.ACTIVE)

        response = member_client.get("/pessoas/")
        assert list(response.context["people"]) == []

    def test_pastor_still_sees_everyone(self, pastor_client, church, department):
        Department.objects.create(church=church, name="Diaconato")
        Person.objects.create(church=church, full_name="Qualquer Um", is_member=True, status=Person.Status.ACTIVE)

        response = pastor_client.get("/pessoas/")
        assert len(response.context["people"]) >= 1
