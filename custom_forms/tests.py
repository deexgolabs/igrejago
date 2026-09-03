import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from custom_forms.models import CustomForm, FormField, FormResponse
from notifications.models import WhatsAppMessage
from people.models import Person


@pytest.fixture
def custom_form(db, church):
    return CustomForm.objects.create(church=church, title="Pedido de oração")


@pytest.mark.django_db
class TestCustomFormPublicUrl:
    def test_relative_when_no_public_link_domain_configured(self, custom_form, settings):
        settings.PUBLIC_LINK_DOMAIN = ""
        assert custom_form.public_url == custom_form.get_absolute_url()

    def test_uses_public_link_domain_when_configured(self, custom_form, settings):
        settings.PUBLIC_LINK_DOMAIN = "https://igrejago.link"
        assert custom_form.public_url == f"https://igrejago.link{custom_form.get_absolute_url()}"


@pytest.fixture
def name_field(custom_form):
    return FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nome", field_type=FormField.FieldType.TEXT,
        order=0, is_name_field=True,
    )


@pytest.fixture
def phone_field(custom_form):
    return FormField.objects.create(
        church=custom_form.church, form=custom_form, label="WhatsApp", field_type=FormField.FieldType.PHONE,
        order=1, is_phone_field=True,
    )


@pytest.fixture
def message_field(custom_form):
    return FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Pedido", field_type=FormField.FieldType.TEXTAREA,
        order=2, required=False,
    )


@pytest.mark.django_db
class TestCustomFormManagement:
    def test_pastor_can_create_form(self, pastor_client):
        response = pastor_client.post("/formularios/novo/", {"title": "Inscrição de batismo"})
        assert response.status_code == 302
        form = CustomForm.objects.get()
        assert form.title == "Inscrição de batismo"
        assert form.slug == "inscricao-de-batismo"

    def test_member_cannot_manage_forms(self, member_client):
        response = member_client.get("/formularios/")
        assert response.status_code == 403

    def test_add_field_to_form(self, pastor_client, custom_form):
        response = pastor_client.post(f"/formularios/{custom_form.pk}/campos/", {
            "label": "Nome completo", "field_type": "TEXT", "options": "", "order": "0",
        })
        assert response.status_code == 302
        assert custom_form.fields.filter(label="Nome completo").exists()

    def test_delete_field(self, pastor_client, custom_form, name_field):
        response = pastor_client.post(f"/formularios/{custom_form.pk}/campos/{name_field.pk}/excluir/")
        assert response.status_code == 302
        assert not FormField.objects.filter(pk=name_field.pk).exists()

    def test_cannot_enable_whatsapp_dispatch_without_phone_field(self, pastor_client, custom_form, name_field):
        response = pastor_client.post(f"/formularios/{custom_form.pk}/editar/", {
            "title": custom_form.title,
            "description": "",
            "send_whatsapp_confirmation": "on",
            "whatsapp_message_template": custom_form.whatsapp_message_template,
        })
        assert response.status_code == 200  # form_invalid re-renders, não redireciona
        custom_form.refresh_from_db()
        assert custom_form.send_whatsapp_confirmation is False

    def test_can_enable_whatsapp_dispatch_with_phone_field(self, pastor_client, custom_form, phone_field):
        response = pastor_client.post(f"/formularios/{custom_form.pk}/editar/", {
            "title": custom_form.title,
            "description": "",
            "send_whatsapp_confirmation": "on",
            "whatsapp_message_template": custom_form.whatsapp_message_template,
        })
        assert response.status_code == 302
        custom_form.refresh_from_db()
        assert custom_form.send_whatsapp_confirmation is True


@pytest.mark.django_db
class TestPublicFormSubmission:
    def test_inactive_form_returns_404(self, client, custom_form):
        custom_form.is_active = False
        custom_form.save(update_fields=["is_active"])
        response = client.get(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/")
        assert response.status_code == 404

    def test_submit_creates_response_and_answers(self, client, custom_form, name_field, message_field):
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "João Silva",
            f"field_{message_field.pk}": "Por favor orem pela minha família.",
            "privacy_consent": "on",
        })
        assert response.status_code == 302
        form_response = FormResponse.objects.get()
        answers = form_response.answers_by_field_id()
        assert answers[name_field.pk] == "João Silva"
        assert answers[message_field.pk] == "Por favor orem pela minha família."

    def test_required_field_missing_reshows_form_with_error(self, client, custom_form, name_field):
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {f"field_{name_field.pk}": ""})
        assert response.status_code == 200
        assert not FormResponse.objects.exists()
        assert b"obrigat" in response.content.lower()

    def test_optional_field_left_blank_does_not_block_submission(self, client, custom_form, name_field, message_field):
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Ana", f"field_{message_field.pk}": "", "privacy_consent": "on",
        })
        assert response.status_code == 302

    def test_multiple_choice_joins_selected_options(self, client, custom_form):
        field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Ministérios de interesse",
            field_type=FormField.FieldType.MULTIPLE_CHOICE,
            options="Louvor\nInfantil\nDiaconato", required=False,
        )
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {f"field_{field.pk}": ["Louvor", "Infantil"], "privacy_consent": "on"})
        assert response.status_code == 302
        answers = FormResponse.objects.get().answers_by_field_id()
        assert answers[field.pk] == "Louvor, Infantil"


@pytest.mark.django_db
class TestWhatsAppDispatchOnSubmission:
    def test_disabled_dispatch_does_not_queue_message(self, client, custom_form, name_field, phone_field):
        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Carlos", f"field_{phone_field.pk}": "62911112222", "privacy_consent": "on",
        })
        assert not WhatsAppMessage.objects.exists()

    def test_enabled_dispatch_queues_message_with_normalized_phone(self, client, custom_form, name_field, phone_field):
        custom_form.send_whatsapp_confirmation = True
        custom_form.whatsapp_message_template = "Obrigado {nome} por responder {formulario}!"
        custom_form.save()

        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Carlos", f"field_{phone_field.pk}": "(62) 91111-2222", "privacy_consent": "on",
        })
        msg = WhatsAppMessage.objects.get()
        assert msg.phone == "5562911112222"
        assert msg.message == "Obrigado Carlos por responder Pedido de oração!"
        assert msg.status == WhatsAppMessage.Status.PENDING

    def test_enabled_dispatch_without_digits_in_phone_answer_does_not_queue(
        self, client, custom_form, name_field, phone_field
    ):
        custom_form.send_whatsapp_confirmation = True
        custom_form.save()
        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Carlos", f"field_{phone_field.pk}": "", "privacy_consent": "on",
        })
        assert not WhatsAppMessage.objects.exists()


@pytest.mark.django_db
class TestExpandedFieldTypes:
    def test_gender_field_uses_person_gender_choices(self, custom_form):
        field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Sexo", field_type=FormField.FieldType.GENDER)
        assert field.choices_for_render() == list(Person.Gender.choices)

    def test_marital_status_field_uses_person_choices(self, custom_form):
        field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Estado civil", field_type=FormField.FieldType.MARITAL_STATUS,
        )
        assert field.choices_for_render() == list(Person.MaritalStatus.choices)

    def test_state_field_has_27_brazilian_states(self, custom_form):
        field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="UF", field_type=FormField.FieldType.STATE)
        assert len(field.choices_for_render()) == 27
        assert ("GO", "GO") in field.choices_for_render()

    def test_submit_with_birth_date_gender_and_state(self, client, custom_form):
        birth_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nascimento", field_type=FormField.FieldType.BIRTH_DATE)
        gender_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Sexo", field_type=FormField.FieldType.GENDER)
        state_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="UF", field_type=FormField.FieldType.STATE)

        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{birth_field.pk}": "1990-05-20",
            f"field_{gender_field.pk}": "F",
            f"field_{state_field.pk}": "GO",
            "privacy_consent": "on",
        })
        assert response.status_code == 302
        answers = FormResponse.objects.get().answers_by_field_id()
        assert answers[birth_field.pk] == "1990-05-20"
        assert answers[gender_field.pk] == "F"
        assert answers[state_field.pk] == "GO"

    def test_file_field_upload_is_saved(self, client, custom_form):
        file_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE, required=True,
        )
        upload = SimpleUploadedFile("comprovante.txt", b"conteudo do arquivo")
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {f"field_{file_field.pk}": upload, "privacy_consent": "on"})
        assert response.status_code == 302
        answer = FormResponse.objects.get().answers.get(field=file_field)
        assert answer.file.name
        assert answer.file.read() == b"conteudo do arquivo"

    def test_required_file_field_missing_reshows_with_error(self, client, custom_form):
        file_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE, required=True,
        )
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {})
        assert response.status_code == 200
        assert not FormResponse.objects.exists()

    def test_disallowed_extension_is_rejected(self, client, custom_form):
        # Achado numa revisão de segurança: antes disso, qualquer
        # arquivo era aceito sem checar tipo — um visitante anônimo
        # podia subir .html/.exe/etc.
        file_field = FormField.objects.create(
            church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE,
        )
        upload = SimpleUploadedFile("malicioso.html", b"<script>alert(1)</script>")
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{file_field.pk}": upload, "privacy_consent": "on",
        })
        assert response.status_code == 200
        assert not FormResponse.objects.exists()

    def test_oversized_file_is_rejected(self, client, custom_form):
        file_field = FormField.objects.create(
            church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE,
        )
        upload = SimpleUploadedFile("grande.pdf", b"x" * (11 * 1024 * 1024))
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{file_field.pk}": upload, "privacy_consent": "on",
        })
        assert response.status_code == 200
        assert not FormResponse.objects.exists()

    def test_uploaded_file_gets_random_name_not_original(self, client, custom_form):
        file_field = FormField.objects.create(
            church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE,
        )
        upload = SimpleUploadedFile("nome-original-do-arquivo.txt", b"conteudo")
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{file_field.pk}": upload, "privacy_consent": "on",
        })
        assert response.status_code == 302
        answer = FormResponse.objects.get().answers.get(field=file_field)
        assert "nome-original-do-arquivo" not in answer.file.name
        assert answer.file.name.endswith(".txt")


@pytest.mark.django_db
class TestHoneypot:
    def test_filled_honeypot_pretends_success_without_saving(self, client, custom_form, name_field):
        response = client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Bot", "website_confirm": "http://spam.example.com",
        })
        assert response.status_code == 302
        assert response.url.endswith("/obrigado/")
        assert not FormResponse.objects.exists()


@pytest.mark.django_db
class TestSyncToPerson:
    def test_disabled_sync_does_not_touch_people(self, client, custom_form):
        name = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nome", field_type=FormField.FieldType.NAME)
        phone = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Tel", field_type=FormField.FieldType.PHONE)
        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name.pk}": "Carlos Souza", f"field_{phone.pk}": "62911112222", "privacy_consent": "on",
        })
        assert not Person.objects.exists()

    def test_enabled_sync_creates_new_person_as_visitor(self, client, custom_form):
        custom_form.sync_to_person = True
        custom_form.save()
        name = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nome", field_type=FormField.FieldType.NAME)
        phone = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Tel", field_type=FormField.FieldType.PHONE)
        birth = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nascimento", field_type=FormField.FieldType.BIRTH_DATE, required=False)

        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name.pk}": "Carlos Souza", f"field_{phone.pk}": "(62) 91111-2222",
            f"field_{birth.pk}": "1995-01-10", "privacy_consent": "on",
        })
        person = Person.objects.get()
        assert person.full_name == "Carlos Souza"
        assert person.phone == "(62) 91111-2222"
        assert str(person.birth_date) == "1995-01-10"
        assert person.is_visitor is True
        assert FormResponse.objects.get().person == person

    def test_enabled_sync_updates_existing_person_found_by_phone(self, client, custom_form):
        custom_form.sync_to_person = True
        custom_form.save()
        existing = Person.objects.create(church=custom_form.church, full_name="Nome Antigo", phone="62911112222")
        name = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nome", field_type=FormField.FieldType.NAME)
        phone = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Tel", field_type=FormField.FieldType.PHONE)

        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name.pk}": "Nome Atualizado", f"field_{phone.pk}": "62911112222", "privacy_consent": "on",
        })
        assert Person.objects.count() == 1
        existing.refresh_from_db()
        assert existing.full_name == "Nome Atualizado"

    def test_enabled_sync_updates_logged_in_users_own_person_not_a_new_one(self, member_client, member_user, person, custom_form):
        member_user.person = person
        member_user.save()
        custom_form.sync_to_person = True
        custom_form.save()
        email = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="E-mail", field_type=FormField.FieldType.EMAIL)

        member_client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {f"field_{email.pk}": "novo@example.com", "privacy_consent": "on"})
        assert Person.objects.count() == 1
        person.refresh_from_db()
        assert person.email == "novo@example.com"

    def test_sync_never_overwrites_with_blank_answer(self, client, custom_form):
        custom_form.sync_to_person = True
        custom_form.save()
        existing = Person.objects.create(church=custom_form.church, full_name="Alguém", phone="62911112222", email="ja-tenho@example.com")
        name = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Nome", field_type=FormField.FieldType.NAME)
        phone = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Tel", field_type=FormField.FieldType.PHONE)
        email = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="E-mail", field_type=FormField.FieldType.EMAIL, required=False)

        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name.pk}": "Alguém", f"field_{phone.pk}": "62911112222", f"field_{email.pk}": "", "privacy_consent": "on",
        })
        existing.refresh_from_db()
        assert existing.email == "ja-tenho@example.com"


@pytest.mark.django_db
class TestNotifyStaff:
    def test_notify_disabled_sends_no_email(self, client, custom_form, name_field, mailoutbox):
        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {f"field_{name_field.pk}": "Ana", "privacy_consent": "on"})
        assert len(mailoutbox) == 0

    def test_notify_enabled_sends_email_with_answers(self, client, custom_form, name_field, message_field, mailoutbox):
        custom_form.notify_staff_emails = "secretaria@igreja.local, pastor@igreja.local"
        custom_form.save()

        client.post(f"/{custom_form.church.slug}/formularios/{custom_form.slug}/", {
            f"field_{name_field.pk}": "Ana", f"field_{message_field.pk}": "Por favor, orem por mim.", "privacy_consent": "on",
        })
        assert len(mailoutbox) == 1
        mail = mailoutbox[0]
        assert set(mail.to) == {"secretaria@igreja.local", "pastor@igreja.local"}
        assert custom_form.title in mail.subject
        assert "Ana" in mail.body
        assert "orem por mim" in mail.body


@pytest.mark.django_db
class TestDuplicateAndStarterTemplates:
    def test_duplicate_copies_fields_and_starts_inactive(self, pastor_client, custom_form, name_field, phone_field):
        response = pastor_client.post(f"/formularios/{custom_form.pk}/duplicar/")
        assert response.status_code == 302
        clone = CustomForm.objects.exclude(pk=custom_form.pk).get()
        assert clone.title == f"{custom_form.title} (cópia)"
        assert clone.is_active is False
        assert clone.fields.count() == 2

    def test_from_starter_creates_form_with_fields(self, pastor_client):
        response = pastor_client.post("/formularios/modelos/novo/", {"template": "cadastro"})
        assert response.status_code == 302
        form = CustomForm.objects.get(title="Atualização de Cadastro")
        assert form.is_active is False
        assert form.sync_to_person is True
        assert form.fields.count() == 7

    def test_from_starter_unknown_key_redirects_with_error(self, pastor_client):
        response = pastor_client.post("/formularios/modelos/novo/", {"template": "nao-existe"})
        assert response.status_code == 302
        assert not CustomForm.objects.exists()


@pytest.mark.django_db
class TestFormResponseListing:
    def test_response_list_shows_answers_in_field_order(self, pastor_client, custom_form, name_field, phone_field):
        response_obj = FormResponse.objects.create(church=custom_form.church, form=custom_form)
        response_obj.answers.create(church=response_obj.church, field=name_field, value="Beatriz")
        response_obj.answers.create(church=response_obj.church, field=phone_field, value="5562999998888")

        response = pastor_client.get(f"/formularios/{custom_form.pk}/respostas/")
        assert response.status_code == 200
        assert b"Beatriz" in response.content

    def test_response_export_csv(self, pastor_client, custom_form, name_field):
        FormResponse.objects.create(church=custom_form.church, form=custom_form).answers.create(church=custom_form.church, field=name_field, value="Beatriz")
        response = pastor_client.get(f"/formularios/{custom_form.pk}/respostas/exportar/")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert "Beatriz" in response.content.decode("utf-8-sig")

    def test_response_list_shows_download_link_for_file_answer(self, pastor_client, custom_form):
        file_field = FormField.objects.create(
        church=custom_form.church, form=custom_form, label="Comprovante", field_type=FormField.FieldType.FILE)
        response_obj = FormResponse.objects.create(church=custom_form.church, form=custom_form)
        response_obj.answers.create(church=response_obj.church, field=file_field, file=SimpleUploadedFile("nota.txt", b"x"))

        response = pastor_client.get(f"/formularios/{custom_form.pk}/respostas/")
        assert response.status_code == 200
        assert b"Baixar arquivo" in response.content
