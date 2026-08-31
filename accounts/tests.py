import pytest

from accounts.models import TOTPDevice, User
from accounts.totp import generate_secret, totp_now, verify_totp


@pytest.mark.django_db
class TestUserRoleProperties:
    @pytest.mark.parametrize("role,can_manage", [
        (User.Role.PASTOR, True),
        (User.Role.ADMIN, True),
        (User.Role.LEADER, True),
        (User.Role.MEMBER, False),
    ])
    def test_can_manage_people_by_role(self, role, can_manage):
        user = User.objects.create_user(username=f"u-{role}", password="x", role=role)
        assert user.can_manage_people is can_manage

    def test_is_pastor_only_true_for_pastor(self):
        pastor = User.objects.create_user(username="p", password="x", role=User.Role.PASTOR)
        admin = User.objects.create_user(username="a", password="x", role=User.Role.ADMIN)
        assert pastor.is_pastor is True
        assert admin.is_pastor is False


@pytest.mark.django_db
class TestCreateAccess:
    def test_creates_user_and_sends_reset_email(self, pastor_client, person, mailoutbox):
        person.email = "maria@example.com"
        person.save()

        response = pastor_client.post(f"/pessoas/{person.pk}/criar-acesso/")
        assert response.status_code == 302

        user = User.objects.get(person=person)
        assert user.role == User.Role.MEMBER
        assert user.has_usable_password() is True  # senha aleatória, não "unusable" — ver views.py
        assert len(mailoutbox) == 1
        assert mailoutbox[0].to == ["maria@example.com"]

    def test_requires_email_on_person(self, pastor_client, person):
        person.email = ""
        person.save()
        response = pastor_client.post(f"/pessoas/{person.pk}/criar-acesso/")
        assert response.status_code == 302
        assert not User.objects.filter(person=person).exists()

    def test_refuses_duplicate_access(self, pastor_client, person, member_user):
        person.email = "maria@example.com"
        person.save()
        member_user.person = person
        member_user.save()

        response = pastor_client.post(f"/pessoas/{person.pk}/criar-acesso/")
        assert response.status_code == 302
        assert User.objects.filter(person=person).count() == 1


class TestTOTPAlgorithm:
    """Testa `accounts.totp` isolado — sem banco, sem view. Se um app
    autenticador de verdade (Google Authenticator etc.) não bater com
    isso, o bug está aqui, não no fluxo de login."""

    def test_generated_code_verifies_against_its_own_secret(self):
        secret = generate_secret()
        code = totp_now(secret)
        assert verify_totp(secret, code) is True

    def test_wrong_code_fails(self):
        secret = generate_secret()
        assert verify_totp(secret, "000000") is False

    def test_code_for_different_secret_fails(self):
        code = totp_now(generate_secret())
        assert verify_totp(generate_secret(), code) is False

    def test_non_numeric_code_fails_without_raising(self):
        assert verify_totp(generate_secret(), "abcxyz") is False
        assert verify_totp(generate_secret(), "") is False

    def test_code_from_previous_step_within_window_still_verifies(self):
        secret = generate_secret()
        code_30s_ago = totp_now(secret, for_time=1_700_000_000 - 30)
        # `verify_totp` sempre compara contra o relógio real (`time.time()`),
        # então pra testar a janela de tolerância sem depender do horário
        # em que o teste roda, gera o código pro passo atual "manualmente"
        # com a mesma margem que `verify_totp` aceita.
        import time as _time
        from accounts.totp import _hotp
        counter_now = int(_time.time() // 30)
        code_previous_step = _hotp(secret, counter_now - 1)
        assert verify_totp(secret, code_previous_step) is True


@pytest.mark.django_db
class TestTOTPSetupFlow:
    def test_requires_login(self, client):
        response = client.get("/accounts/2fa/configurar/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_get_creates_pending_device_and_shows_qr(self, pastor_client, pastor_user):
        response = pastor_client.get("/accounts/2fa/configurar/")
        assert response.status_code == 200
        device = TOTPDevice.objects.get(user=pastor_user)
        assert device.confirmed is False
        assert b"data:image/png;base64," in response.content

    def test_valid_code_confirms_device(self, pastor_client, pastor_user):
        pastor_client.get("/accounts/2fa/configurar/")
        device = TOTPDevice.objects.get(user=pastor_user)
        response = pastor_client.post("/accounts/2fa/configurar/", {"code": totp_now(device.secret)})
        assert response.status_code == 302
        device.refresh_from_db()
        assert device.confirmed is True

    def test_invalid_code_does_not_confirm(self, pastor_client, pastor_user):
        pastor_client.get("/accounts/2fa/configurar/")
        pastor_client.post("/accounts/2fa/configurar/", {"code": "000000"})
        device = TOTPDevice.objects.get(user=pastor_user)
        assert device.confirmed is False

    def test_disable_removes_device(self, pastor_client, pastor_user):
        TOTPDevice.objects.create(user=pastor_user, secret=generate_secret(), confirmed=True)
        response = pastor_client.post("/accounts/2fa/desativar/")
        assert response.status_code == 302
        assert not TOTPDevice.objects.filter(user=pastor_user).exists()


@pytest.mark.django_db
class TestLoginWithTwoFactor:
    def test_login_without_device_works_as_before(self, client, pastor_user):
        response = client.post("/accounts/login/", {"username": "pastor", "password": "teste12345"})
        assert response.status_code == 302
        assert response.url != "/accounts/2fa/verificar/"

    def test_login_with_confirmed_device_does_not_log_in_yet(self, client, pastor_user):
        TOTPDevice.objects.create(user=pastor_user, secret=generate_secret(), confirmed=True)
        response = client.post("/accounts/login/", {"username": "pastor", "password": "teste12345"})
        assert response.status_code == 302
        assert response.url == "/accounts/2fa/verificar/"
        assert "_auth_user_id" not in client.session

    def test_correct_totp_code_completes_login(self, client, pastor_user):
        device = TOTPDevice.objects.create(user=pastor_user, secret=generate_secret(), confirmed=True)
        client.post("/accounts/login/", {"username": "pastor", "password": "teste12345"})
        response = client.post("/accounts/2fa/verificar/", {"code": totp_now(device.secret)})
        assert response.status_code == 302
        assert str(client.session["_auth_user_id"]) == str(pastor_user.pk)

    def test_wrong_totp_code_does_not_complete_login(self, client, pastor_user):
        TOTPDevice.objects.create(user=pastor_user, secret=generate_secret(), confirmed=True)
        client.post("/accounts/login/", {"username": "pastor", "password": "teste12345"})
        response = client.post("/accounts/2fa/verificar/", {"code": "000000"})
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session

    def test_verify_without_pending_login_redirects_to_login(self, client):
        response = client.get("/accounts/2fa/verificar/")
        assert response.status_code == 302
        assert response.url == "/accounts/login/"


@pytest.mark.django_db
class TestAdminLoginRedirectsToAppLogin:
    def test_admin_login_page_redirects(self, client):
        response = client.get("/admin/login/")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_unauthenticated_admin_access_redirects_through_app_login(self, client):
        # Django primeiro redireciona pra /admin/login/ (padrão do
        # `AdminSite`); só quando ESSA página é de fato pedida é que
        # `admin.site.login` (nossa versão sobrescrita) entra em ação —
        # por isso `follow=True` aqui, pra conferir o destino final, não
        # só o primeiro salto.
        response = client.get("/admin/core/church/", follow=True)
        final_url, _ = response.redirect_chain[-1]
        assert "/accounts/login/" in final_url


@pytest.mark.django_db
class TestMandatoryTwoFactorForStaff:
    """`is_staff` (o "dono") agora precisa de 2FA confirmado pra acessar o
    Django admin — ver `accounts/apps.py::AccountsConfig.ready()`."""

    def test_staff_without_2fa_is_redirected_to_setup(self, client):
        staff_user = User.objects.create_user(username="dono", password="x", is_staff=True)
        client.force_login(staff_user)
        response = client.get("/admin/", follow=True)
        final_url, _ = response.redirect_chain[-1]
        assert final_url == "/accounts/2fa/configurar/"

    def test_staff_with_confirmed_2fa_can_access_admin(self, client):
        staff_user = User.objects.create_user(username="dono2", password="x", is_staff=True)
        TOTPDevice.objects.create(user=staff_user, secret=generate_secret(), confirmed=True)
        client.force_login(staff_user)
        response = client.get("/admin/")
        assert response.status_code == 200

    def test_staff_with_unconfirmed_device_still_blocked(self, client):
        staff_user = User.objects.create_user(username="dono3", password="x", is_staff=True)
        TOTPDevice.objects.create(user=staff_user, secret=generate_secret(), confirmed=False)
        client.force_login(staff_user)
        response = client.get("/admin/", follow=True)
        final_url, _ = response.redirect_chain[-1]
        assert final_url == "/accounts/2fa/configurar/"

    def test_non_staff_authenticated_user_hitting_admin_goes_to_dashboard(self, member_client):
        response = member_client.get("/admin/", follow=True)
        final_url, _ = response.redirect_chain[-1]
        assert final_url == "/"
