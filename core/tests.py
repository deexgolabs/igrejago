import pytest

from core.colors import generate_palette
from core.whatsapp import enviar_whatsapp


class TestColorPalette:
    def test_generates_all_tailwind_shades(self):
        palette = generate_palette("#2563eb")
        for shade in ("50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"):
            assert shade in palette
            assert palette[shade].startswith("#")

    def test_invalid_hex_falls_back_without_crashing(self):
        palette = generate_palette("not-a-color")
        assert palette["600"].startswith("#")


@pytest.mark.django_db
class TestDashboardBranchesByRole:
    def test_pastor_sees_admin_dashboard(self, pastor_client):
        response = pastor_client.get("/")
        assert "core/dashboard.html" in [t.name for t in response.templates]

    def test_member_sees_portal(self, member_client):
        response = member_client.get("/")
        assert "core/member_portal.html" in [t.name for t in response.templates]

    def test_member_portal_shows_own_data_when_linked(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.get("/")
        assert person.full_name.encode() in response.content

    def test_member_portal_handles_unlinked_user(self, member_client):
        response = member_client.get("/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestWhatsAppConsoleFallback:
    def test_returns_true_without_api_configured(self, church_config, capsys):
        ok, error, external_id = enviar_whatsapp("62999998888", "Olá! 🎉", church_config=church_config)
        assert ok is True
        assert error == ""
        assert external_id == ""
        assert "62999998888" in capsys.readouterr().out

    def test_returns_false_without_phone(self, church_config):
        ok, error, external_id = enviar_whatsapp("", "mensagem", church_config=church_config)
        assert ok is False
        assert error
        assert external_id == ""


@pytest.mark.django_db
class TestWhatsAppWebhookPayloadShape:
    """`enabled: true` é exigido pela Evolution API — confirmado ao vivo
    (400 Bad Request sem ele em `/webhook/set/`, aceito sem em
    `/instance/create`, mas incluído nos dois por segurança/consistência).
    Sem esse teste, uma futura edição podia remover o campo de novo e só
    quebrar contra um servidor real, não na suíte."""

    def test_criar_instancia_embeds_enabled_true_in_webhook(self, church_config, settings):
        from unittest.mock import patch, Mock
        from core.whatsapp import criar_instancia

        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        with patch("core.whatsapp.requests.post") as mock_post:
            mock_post.return_value = Mock(json=lambda: {"hash": "x"}, raise_for_status=lambda: None)
            criar_instancia(
                church_config, instance_name="igreja-x",
                webhook_url="https://example.com/webhook/", webhook_secret="segredo",
            )
        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["webhook"]["enabled"] is True

    def test_configurar_webhook_sends_enabled_true(self, church_config, settings):
        from unittest.mock import patch, Mock
        from core.whatsapp import configurar_webhook

        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        with patch("core.whatsapp.requests.post") as mock_post:
            mock_post.return_value = Mock(json=lambda: {}, raise_for_status=lambda: None)
            configurar_webhook(
                church_config, instance_name="igreja-x",
                webhook_url="https://example.com/webhook/", webhook_secret="segredo",
            )
        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["webhook"]["enabled"] is True


@pytest.mark.django_db
class TestGeneralReportPDF:
    def test_generates_valid_pdf(self, church_config):
        from core.reports import generate_general_report_pdf
        pdf_bytes = generate_general_report_pdf(church_config)
        assert pdf_bytes.startswith(b"%PDF-")

    def test_pdf_download_requires_management_permission(self, member_client):
        response = member_client.get("/relatorio.pdf")
        assert response.status_code == 403

    def test_pdf_download_works_for_pastor(self, pastor_client):
        response = pastor_client.get("/relatorio.pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
class TestPWAEndpoints:
    def test_manifest_returns_valid_json(self, client, church_config):
        response = client.get("/manifest.json")
        assert response.status_code == 200
        assert response.json()["theme_color"] == church_config.brand_color

    def test_service_worker_served_at_root(self, client):
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/javascript"


@pytest.mark.django_db
class TestAuditLog:
    def test_creating_person_logs_audit_entry(self, pastor_client, pastor_user):
        from core.models import AuditLog

        pastor_client.post("/pessoas/novo/", {
            "full_name": "Log Teste", "role": "MEMBER", "status": "ACTIVE", "is_member": "on",
        })
        entry = AuditLog.objects.filter(model_name="Person", object_repr="Log Teste").first()
        assert entry is not None
        assert entry.action == AuditLog.Action.CREATE
        assert entry.user == pastor_user


@pytest.mark.django_db
class TestAuditLogScreen:
    def test_member_cannot_view(self, member_client):
        response = member_client.get("/auditoria/")
        assert response.status_code == 403

    def test_pastor_sees_entries(self, pastor_client, pastor_user):
        from core.models import AuditLog

        AuditLog.objects.create(
            church=pastor_user.church,
            user=pastor_user, action=AuditLog.Action.CREATE, model_name="Person", object_repr="Fulano",
        )
        response = pastor_client.get("/auditoria/")
        assert response.status_code == 200
        assert b"Fulano" in response.content

    def test_filters_by_model_name(self, pastor_client, pastor_user):
        from core.models import AuditLog

        AuditLog.objects.create(
            church=pastor_user.church,
            user=pastor_user, action=AuditLog.Action.CREATE, model_name="Person", object_repr="Pessoa X",
        )
        AuditLog.objects.create(
            church=pastor_user.church,
            user=pastor_user, action=AuditLog.Action.DELETE, model_name="Transaction", object_repr="Lançamento Y",
        )

        response = pastor_client.get("/auditoria/?model_name=Transaction")
        assert b"Lan\xc3\xa7amento Y" in response.content
        assert b"Pessoa X" not in response.content


@pytest.mark.django_db
class TestEnviarLembretesQueuesInsteadOfSending:
    def test_birthday_reminder_is_queued_not_sent(self, church_config):
        from datetime import date

        from django.core.management import call_command

        from notifications.models import WhatsAppMessage
        from people.models import Person

        today = date.today()
        Person.objects.create(
            church=church_config,
            full_name="Aniversariante", phone="62911112222",
            birth_date=today.replace(year=1990),
        )
        call_command("enviar_lembretes")

        msg = WhatsAppMessage.objects.get()
        assert msg.status == WhatsAppMessage.Status.PENDING
        assert msg.campaign_label == "Lembrete de aniversário"

    def test_person_without_phone_is_not_queued(self, church_config):
        from datetime import date

        from django.core.management import call_command

        from notifications.models import WhatsAppMessage
        from people.models import Person

        today = date.today()
        Person.objects.create(church=church_config, full_name="Sem Telefone", birth_date=today.replace(year=1990))
        call_command("enviar_lembretes")
        assert not WhatsAppMessage.objects.exists()


@pytest.fixture
def admin_client_(client, django_user_model):
    from accounts.models import TOTPDevice
    from accounts.totp import generate_secret

    superuser = django_user_model.objects.create_superuser(username="root", password="x", email="")
    # `is_staff` agora exige 2FA confirmado pra passar em
    # `admin.site.has_permission` (ver accounts/apps.py) — sem isso, todo
    # teste que usa esta fixture pra acessar o admin cairia no redirect
    # de 2FA pendente em vez de chegar na página testada.
    TOTPDevice.objects.create(user=superuser, secret=generate_secret(), confirmed=True)
    client.force_login(superuser)
    return client


@pytest.mark.django_db
class TestWhatsAppConnectionAdmin:
    def test_change_form_renders_without_configured_connection(self, admin_client_, church_config):
        response = admin_client_.get(f"/admin/core/church/{church_config.pk}/change/")
        assert response.status_code == 200
        assert b"Criar/recriar inst\xc3\xa2ncia" in response.content

    def test_create_instance_without_url_shows_error_and_redirects(self, admin_client_, church_config):
        response = admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/criar-instancia/")
        assert response.status_code == 302

    def test_qrcode_without_config_redirects_with_error(self, admin_client_, church_config):
        response = admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/qrcode/")
        assert response.status_code == 302

    def test_disconnect_without_config_redirects_with_error(self, admin_client_, church_config):
        response = admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/desconectar/")
        assert response.status_code == 302

    def test_create_instance_generates_webhook_secret_and_configures_it(self, admin_client_, church_config, settings):
        """`criar_instancia()` agora embute a configuração do webhook de
        confirmação de entrega direto na chamada de criação — não é mais
        um passo manual (ver README/DEPLOY.md). Confirma que a view gera
        um segredo (se a igreja ainda não tiver um) e passa a URL/segredo
        certos pra `core.whatsapp.criar_instancia()`."""
        from unittest.mock import patch

        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        church_config.whatsapp_webhook_secret = ""
        church_config.save()

        with patch("core.admin.whatsapp.criar_instancia", return_value={"hash": "novo-token"}) as mock_criar:
            response = admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/criar-instancia/")
        assert response.status_code == 302

        church_config.refresh_from_db()
        assert church_config.whatsapp_webhook_secret  # foi gerado, não ficou em branco

        _, kwargs = mock_criar.call_args
        assert kwargs["webhook_secret"] == church_config.whatsapp_webhook_secret
        assert kwargs["webhook_url"].endswith("/mensagens/webhook/evolution/")

    def test_create_instance_reuses_existing_webhook_secret(self, admin_client_, church_config, settings):
        from unittest.mock import patch

        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        church_config.whatsapp_webhook_secret = "ja-existia"
        church_config.save()

        with patch("core.admin.whatsapp.criar_instancia", return_value={"hash": "novo-token"}) as mock_criar:
            admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/criar-instancia/")

        church_config.refresh_from_db()
        assert church_config.whatsapp_webhook_secret == "ja-existia"
        assert mock_criar.call_args.kwargs["webhook_secret"] == "ja-existia"

    def test_create_instance_falls_back_to_webhook_config_when_already_exists(
        self, admin_client_, church_config, settings
    ):
        """Confirmado ao vivo contra um servidor Evolution real: recriar
        uma instância já existente (já conectada) devolve 403 "already in
        use" — a view não pode tratar isso como falha, senão ninguém
        conseguiria reconfigurar o webhook de uma igreja que já conectou
        o WhatsApp antes dessa correção existir."""
        import requests
        from unittest.mock import patch, Mock

        settings.EVOLUTION_API_URL = "https://fake.example.com"
        settings.EVOLUTION_API_KEY = "global-key"
        church_config.whatsapp_webhook_secret = ""
        church_config.save()

        fake_response = Mock(status_code=403, text='{"response":{"message":["already in use"]}}')
        http_error = requests.HTTPError(response=fake_response)

        with patch("core.admin.whatsapp.criar_instancia", side_effect=http_error), \
             patch("core.admin.whatsapp.configurar_webhook") as mock_configurar:
            response = admin_client_.get(f"/admin/core/church/{church_config.pk}/whatsapp/criar-instancia/")

        assert response.status_code == 302
        mock_configurar.assert_called_once()
        church_config.refresh_from_db()
        assert church_config.whatsapp_webhook_secret  # segredo foi gerado mesmo com a instância já existindo


@pytest.mark.django_db
class TestManualView:
    """Manual de configuração dentro do próprio app (pedido do usuário —
    antes só existia como artifact fora do sistema). Visível pra
    qualquer conta logada; as seções de Gestão da plataforma/domínios
    só aparecem pro dono da plataforma."""

    def test_requires_login(self, client):
        response = client.get("/manual/")
        assert response.status_code == 302

    def test_pastor_can_view(self, pastor_client):
        response = pastor_client.get("/manual/")
        assert response.status_code == 200

    def test_member_can_view(self, member_client):
        response = member_client.get("/manual/")
        assert response.status_code == 200

    def test_platform_owner_sees_gestao_section(self, platform_owner_client):
        response = platform_owner_client.get("/manual/")
        assert response.status_code == 200
        assert "Gestão da plataforma".encode() in response.content

    def test_church_user_does_not_see_gestao_section(self, pastor_client):
        # "Gestão da plataforma" sozinho aparece na visão geral (explica os
        # dois tipos de conta pra todo mundo) — o que precisa ficar restrito
        # é a seção com o passo a passo em si, não a menção ao nome.
        response = pastor_client.get("/manual/")
        assert "id=\"gestao\"".encode() not in response.content


@pytest.mark.django_db
class TestSettingsView:
    def test_pastor_can_view_settings(self, pastor_client, church_config):
        response = pastor_client.get("/configuracoes/")
        assert response.status_code == 200

    def test_member_cannot_view_settings(self, member_client):
        response = member_client.get("/configuracoes/")
        assert response.status_code == 403

    def test_saving_updates_church_config(self, pastor_client, church_config):
        response = pastor_client.post("/configuracoes/", {
            "name": "Igreja Nova",
            "pastor_name": "Pastor Teste",
            "brand_color": "#ff0000",
            "whatsapp_absence_template": "Oi {nome}, sentimos falta. {pastor}",
            "whatsapp_birthday_template": "Feliz niver {nome}! {pastor}",
            "whatsapp_send_interval_seconds": "10",
            "whatsapp_batch_size": "20",
            "whatsapp_max_retries": "5",
            "pix_key_type": "",
        })
        assert response.status_code == 302
        church_config.refresh_from_db()
        assert church_config.name == "Igreja Nova"
        assert church_config.whatsapp_send_interval_seconds == 10
        assert church_config.whatsapp_max_retries == 5

    def test_admin_alert_emails_can_be_saved(self, pastor_client, church_config):
        response = pastor_client.post("/configuracoes/", {
            "name": "Igreja Nova", "pastor_name": "", "brand_color": "#2563eb",
            "whatsapp_absence_template": "Oi {nome}", "whatsapp_birthday_template": "Niver {nome}",
            "whatsapp_send_interval_seconds": "6", "whatsapp_batch_size": "30", "whatsapp_max_retries": "3",
            "admin_alert_emails": "dono@example.com, secretaria@example.com",
            "pix_key_type": "",
        })
        assert response.status_code == 302
        church_config.refresh_from_db()
        assert church_config.admin_alert_emails == "dono@example.com, secretaria@example.com"

    def test_settings_form_ignores_posted_technical_whatsapp_fields(self, pastor_client, church_config):
        """A igreja não pode alterar a conexão Evolution API (infra do dono)
        nem enviando os campos direto no POST — eles simplesmente não
        existem em `ChurchConfigForm.Meta.fields`."""
        response = pastor_client.post("/configuracoes/", {
            "name": "Igreja Nova",
            "pastor_name": "Pastor Teste",
            "brand_color": "#ff0000",
            "whatsapp_absence_template": "Oi {nome}",
            "whatsapp_birthday_template": "Niver {nome}",
            "whatsapp_send_interval_seconds": "10",
            "whatsapp_batch_size": "20",
            "whatsapp_max_retries": "5",
            "pix_key_type": "",
            "whatsapp_api_url": "https://malicious.example.com",
            "whatsapp_api_key": "hacked-key",
            "whatsapp_instance": "hacked-instance",
        })
        assert response.status_code == 302
        church_config.refresh_from_db()
        assert church_config.whatsapp_api_url != "https://malicious.example.com"
        assert church_config.whatsapp_api_key != "hacked-key"
        assert church_config.whatsapp_instance != "hacked-instance"


class TestHealthCheck:
    @pytest.mark.django_db
    def test_returns_ok_without_authentication(self, client):
        response = client.get("/health/")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "database": True}


@pytest.mark.django_db
class TestBackupCommand:
    def test_backup_creates_db_and_media_archive(self, settings, tmp_path, monkeypatch):
        from django.core.management import call_command

        settings.BASE_DIR = tmp_path
        db_file = tmp_path / "fake_db.sqlite3"
        db_file.write_bytes(b"fake db content")
        monkeypatch.setitem(settings.DATABASES["default"], "NAME", str(db_file))
        settings.MEDIA_ROOT = tmp_path / "media"
        settings.MEDIA_ROOT.mkdir()
        (settings.MEDIA_ROOT / "foto.jpg").write_bytes(b"fake image content")

        call_command("backup_banco", keep=5)

        backups_dir = tmp_path / "backups"
        assert list(backups_dir.glob("db-*.sqlite3"))
        assert list(backups_dir.glob("media-*.zip"))

    def test_no_media_flag_skips_media_backup(self, settings, tmp_path, monkeypatch):
        from django.core.management import call_command

        settings.BASE_DIR = tmp_path
        db_file = tmp_path / "fake_db.sqlite3"
        db_file.write_bytes(b"fake db content")
        monkeypatch.setitem(settings.DATABASES["default"], "NAME", str(db_file))
        settings.MEDIA_ROOT = tmp_path / "media"
        settings.MEDIA_ROOT.mkdir()
        (settings.MEDIA_ROOT / "foto.jpg").write_bytes(b"x")

        call_command("backup_banco", keep=5, no_media=True)

        backups_dir = tmp_path / "backups"
        assert list(backups_dir.glob("db-*.sqlite3"))
        assert not list(backups_dir.glob("media-*.zip"))

    def test_rotation_keeps_only_n_most_recent(self, settings, tmp_path, monkeypatch):
        from django.core.management import call_command

        settings.BASE_DIR = tmp_path
        db_file = tmp_path / "fake_db.sqlite3"
        db_file.write_bytes(b"fake db content")
        monkeypatch.setitem(settings.DATABASES["default"], "NAME", str(db_file))

        for _ in range(3):
            call_command("backup_banco", keep=2, no_media=True)

        backups_dir = tmp_path / "backups"
        assert len(list(backups_dir.glob("db-*.sqlite3"))) == 2

    def test_postgres_engine_skips_db_but_still_backs_up_media(self, settings, tmp_path, monkeypatch):
        from django.core.management import call_command

        settings.BASE_DIR = tmp_path
        monkeypatch.setitem(settings.DATABASES["default"], "ENGINE", "django.db.backends.postgresql")
        settings.MEDIA_ROOT = tmp_path / "media"
        settings.MEDIA_ROOT.mkdir()
        (settings.MEDIA_ROOT / "foto.jpg").write_bytes(b"x")

        call_command("backup_banco", keep=5)

        backups_dir = tmp_path / "backups"
        assert not list(backups_dir.glob("db-*.sqlite3"))
        assert list(backups_dir.glob("media-*.zip"))


@pytest.mark.django_db
class TestRateLimit:
    def test_login_gets_429_after_too_many_attempts(self, client, pastor_user, settings):
        from django.core.cache import cache
        cache.clear()

        for _ in range(10):
            response = client.post("/accounts/login/", {"username": "pastor", "password": "errada"})
            assert response.status_code == 200
        response = client.post("/accounts/login/", {"username": "pastor", "password": "errada"})
        assert response.status_code == 429

    def test_correct_login_still_works_under_the_limit(self, client, pastor_user):
        from django.core.cache import cache
        cache.clear()

        response = client.post("/accounts/login/", {"username": "pastor", "password": "teste12345"})
        assert response.status_code == 302


@pytest.mark.django_db
class TestMultiCampus:
    def test_pastor_creates_filial(self, pastor_client, church):
        response = pastor_client.post("/rede/nova/", {
            "church_name": "Congregação Bairro Novo", "pastor_name": "Pr. João",
            "username": "pr-joao-filial", "email": "joao@example.com", "password": "senhaSegura123",
        })
        assert response.status_code == 302
        from core.models import Church
        filial = Church.objects.get(name="Congregação Bairro Novo")
        assert filial.matriz_id == church.pk
        assert filial.status == Church.Status.TRIAL

        from accounts.models import User
        novo_user = User.objects.get(username="pr-joao-filial")
        assert novo_user.church_id == filial.pk
        assert novo_user.role == User.Role.PASTOR

    def test_member_cannot_create_filial(self, member_client):
        assert member_client.get("/rede/nova/").status_code == 403

    def test_switching_to_filial_isolates_data(self, pastor_client, pastor_user, church):
        from core.models import Church
        from people.models import Person

        filial = Church.objects.create(name="Filial", matriz=church, email_confirmed=True, status=Church.Status.TRIAL)
        Person.objects.create(church=filial, full_name="Pessoa da Filial")
        Person.objects.create(church=church, full_name="Pessoa da Matriz")

        # Ainda na matriz — só vê a pessoa da matriz.
        response = pastor_client.get("/pessoas/")
        assert b"Pessoa da Matriz" in response.content
        assert b"Pessoa da Filial" not in response.content

        pastor_client.post("/trocar-unidade/", {"church_id": filial.pk})
        response = pastor_client.get("/pessoas/")
        assert b"Pessoa da Filial" in response.content
        assert b"Pessoa da Matriz" not in response.content

        # Volta pra matriz.
        pastor_client.post("/trocar-unidade/", {"church_id": church.pk})
        response = pastor_client.get("/pessoas/")
        assert b"Pessoa da Matriz" in response.content

    def test_cannot_switch_to_unrelated_church(self, pastor_client, outra_church):
        response = pastor_client.post("/trocar-unidade/", {"church_id": outra_church.pk})
        assert response.status_code == 302
        # A sessão não deve ter sido trocada pra uma igreja alheia.
        session = pastor_client.session
        assert session.get("active_church_id") != outra_church.pk

    def test_consolidated_dashboard_sums_network(self, pastor_client, church):
        from core.models import Church
        from people.models import Person

        filial = Church.objects.create(name="Filial", matriz=church, email_confirmed=True, status=Church.Status.TRIAL)
        Person.objects.create(church=church, full_name="Da Matriz")
        Person.objects.create(church=filial, full_name="Da Filial 1")
        Person.objects.create(church=filial, full_name="Da Filial 2")

        response = pastor_client.get("/rede/consolidado/")
        assert response.status_code == 200
        assert response.context["total_pessoas"] == 3


class TestMediaUrl:
    def test_media_url_is_absolute(self, settings):
        """Regressão: `MEDIA_URL` sem barra no início ("media/" em vez de
        "/media/") faz `ImageFieldFile.url` virar uma URL RELATIVA — o
        navegador resolve a logo da igreja relativo à página atual em vez
        da raiz do site, então ela só carrega em páginas na raiz e quebra
        em qualquer rota aninhada (ex.: /mensagens/). Achado ao investigar
        um relato real de "logo ficou quebrada"."""
        assert settings.MEDIA_URL.startswith("/")


@pytest.mark.django_db
class TestShortLinkRedirect:
    def test_redirects_to_target_path(self, client, church):
        from core.models import ShortLink

        ShortLink.objects.create(
            church=church, slug="esperanca-pontal-sul", label="Link da Bio",
            target_path="/esperanca-pontal-sul/links/links/",
        )
        response = client.get("/esperanca-pontal-sul/")
        assert response.status_code == 302
        assert response.url == "/esperanca-pontal-sul/links/links/"
        assert response["Cache-Control"] == "no-store"

    def test_increments_click_count(self, client, church):
        from core.models import ShortLink

        link = ShortLink.objects.create(
            church=church, slug="batismo", label="Inscrição", target_path="/x/formularios/batismo/",
        )
        client.get("/batismo/")
        client.get("/batismo/")
        link.refresh_from_db()
        assert link.click_count == 2

    def test_unknown_slug_is_404(self, client):
        response = client.get("/nao-existe-esse-link/")
        assert response.status_code == 404

    def test_works_for_anonymous_visitor_regardless_of_church(self, client, church, outra_church):
        """O redirect não depende de igreja logada/atual — é resolvido
        só pelo slug, igual uma página pública comum (ver
        `core.tenancy.TenantManager`, `todas_as_igrejas`)."""
        from core.models import ShortLink

        ShortLink.objects.create(
            church=outra_church, slug="outra-igreja", label="Bio", target_path="/outra/links/links/",
        )
        response = client.get("/outra-igreja/")
        assert response.status_code == 302
        assert response.url == "/outra/links/links/"


@pytest.mark.django_db
class TestShortLinkManagement:
    def test_pastor_can_create_short_link(self, pastor_client, church):
        response = pastor_client.post("/links-curtos/novo/", {
            "slug": "meu-link", "label": "Teste", "target_path": "/algum/caminho/",
        })
        assert response.status_code == 302
        from core.models import ShortLink
        link = ShortLink.objects.get(slug="meu-link")
        assert link.church_id == church.id
        assert link.target_path == "/algum/caminho/"

    def test_member_cannot_manage_short_links(self, member_client):
        assert member_client.get("/links-curtos/").status_code == 403
        assert member_client.post("/links-curtos/novo/", {
            "slug": "x", "label": "x", "target_path": "/x/",
        }).status_code == 403

    def test_department_leader_cannot_manage_short_links(self, department_leader_client):
        """Links curtos são infraestrutura da igreja inteira (domínio
        curto compartilhado) — mesmo escopo de `IsChurchManagerMixin`
        usado em Configurações/Eventos/Financeiro, não o de
        `CanManagePeopleMixin` (Líder de Departamento)."""
        assert department_leader_client.get("/links-curtos/").status_code == 403

    def test_reserved_slug_is_rejected(self, pastor_client):
        response = pastor_client.post("/links-curtos/novo/", {
            "slug": "admin", "label": "Tentativa", "target_path": "/x/",
        })
        assert response.status_code == 200  # re-renderiza o form com erro
        from core.models import ShortLink
        assert not ShortLink.objects.filter(slug="admin").exists()

    def test_slug_must_be_globally_unique(self, pastor_client, church):
        from core.models import ShortLink

        ShortLink.objects.create(church=church, slug="ocupado", label="Já existe", target_path="/a/")
        response = pastor_client.post("/links-curtos/novo/", {
            "slug": "ocupado", "label": "Duplicado", "target_path": "/b/",
        })
        assert response.status_code == 200
        assert ShortLink.objects.filter(slug="ocupado").count() == 1

    def test_pasting_a_full_url_keeps_only_the_path(self, pastor_client):
        """`target_path` aceita colar a URL completa (ex.: copiada da
        barra de endereço) — só o caminho é gravado, nunca o domínio."""
        response = pastor_client.post("/links-curtos/novo/", {
            "slug": "colado", "label": "Colado",
            "target_path": "https://churchcrm.redecorp.co/esperanca-pontal-sul/links/links/",
        })
        assert response.status_code == 302
        from core.models import ShortLink
        link = ShortLink.objects.get(slug="colado")
        assert link.target_path == "/esperanca-pontal-sul/links/links/"

    def test_pastor_can_delete_short_link(self, pastor_client, church):
        from core.models import ShortLink

        link = ShortLink.objects.create(church=church, slug="apagar", label="X", target_path="/x/")
        response = pastor_client.post(f"/links-curtos/{link.pk}/excluir/")
        assert response.status_code == 302
        assert not ShortLink.objects.filter(pk=link.pk).exists()


@pytest.mark.django_db
class TestPublicUrlUsesShortLinkWhenAvailable:
    def test_biopage_public_url_prefers_short_link(self, church, settings):
        from core.models import ShortLink
        from linkbio.models import BioPage

        settings.PUBLIC_LINK_DOMAIN = "https://igrejago.link"
        page = BioPage.objects.create(church=church, church_name=church.name)
        long_url = f"https://igrejago.link{page.get_absolute_url()}"
        assert page.public_url == long_url

        ShortLink.objects.create(
            church=church, slug="minha-igreja", label="Bio", target_path=page.get_absolute_url(),
        )
        assert page.public_url == "https://igrejago.link/minha-igreja"
