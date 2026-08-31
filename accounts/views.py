from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View

from accounts.models import TOTPDevice, User
from accounts.totp import generate_secret, otpauth_uri, verify_totp
from core.qr import qr_data_uri
from core.ratelimit import RateLimitMixin


class RateLimitedLoginView(RateLimitMixin, auth_views.LoginView):
    """Login com rate limit por IP (sem isso, nada impedia um brute-force
    de senha direto na tela de login) e com um segundo fator opcional
    (TOTP): se a conta tem um `TOTPDevice` confirmado, usuário/senha
    corretos NÃO logam direto — ficam pendentes na sessão até o código de
    6 dígitos ser confirmado em `TOTPVerifyView`. Sem 2FA configurado, o
    login funciona exatamente como sempre."""

    rate_limit_key = "login"
    rate_limit_max = 10
    rate_limit_window_seconds = 300

    def form_valid(self, form):
        user = form.get_user()
        device = getattr(user, "totp_device", None)
        if device and device.confirmed:
            self.request.session["pending_2fa_user_id"] = user.pk
            self.request.session["pending_2fa_next"] = self.get_success_url()
            return redirect("accounts:totp_verify")
        return super().form_valid(form)


class TOTPVerifyView(RateLimitMixin, View):
    """Segunda etapa do login — só existe uma sessão "pendente" pra
    verificar depois que `RateLimitedLoginView` já conferiu usuário/senha;
    sem isso no `session`, não tem o que verificar. Rate limit próprio
    (chave separada da do login) porque adivinhar um código de 6 dígitos é
    um espaço de busca bem menor do que uma senha."""

    template_name = "accounts/totp_verify.html"
    rate_limit_key = "totp_verify"
    rate_limit_max = 10
    rate_limit_window_seconds = 300

    def get(self, request):
        if not request.session.get("pending_2fa_user_id"):
            return redirect("accounts:login")
        return render(request, self.template_name)

    def post(self, request):
        user_id = request.session.get("pending_2fa_user_id")
        if not user_id:
            return redirect("accounts:login")

        code = request.POST.get("code", "")
        user = User.objects.filter(pk=user_id).first()
        device = getattr(user, "totp_device", None) if user else None
        if user and device and device.confirmed and verify_totp(device.secret, code):
            del request.session["pending_2fa_user_id"]
            next_url = request.session.pop("pending_2fa_next", "/")
            # `login()` normalmente pega o backend de quem chamou
            # `authenticate()` — como esse usuário foi recarregado do banco
            # (não é o mesmo objeto que `AuthenticationForm` autenticou),
            # `.backend` não está setado; o projeto não define
            # AUTHENTICATION_BACKENDS custom, então o padrão é seguro aqui.
            user.backend = "django.contrib.auth.backends.ModelBackend"
            auth_login(request, user)
            return redirect(next_url)

        messages.error(request, "Código inválido. Confira o app autenticador e tente de novo.")
        return render(request, self.template_name)


class TOTPStatusView(LoginRequiredMixin, View):
    """Tela "Segurança" — mostra se a conta tem 2FA ativado e dá acesso a
    configurar ou desativar."""

    template_name = "accounts/totp_status.html"

    def get(self, request):
        device = getattr(request.user, "totp_device", None)
        return render(request, self.template_name, {"device": device})


class TOTPSetupView(LoginRequiredMixin, View):
    """Gera (ou reaproveita) um segredo pendente e mostra o QR code pra
    escanear; só vira "ativo" (`confirmed=True`) depois de digitar um
    código válido de volta, provando que o app autenticador está mesmo
    configurado certo — sem isso, alguém poderia acidentalmente se trancar
    fora da própria conta com um segredo que nunca funcionou."""

    template_name = "accounts/totp_setup.html"

    def get(self, request):
        device, _ = TOTPDevice.objects.get_or_create(
            user=request.user, defaults={"secret": generate_secret()}
        )
        if device.confirmed:
            return redirect("accounts:totp_status")
        return render(request, self.template_name, self._context(device, request.user))

    def post(self, request):
        device = get_object_or_404(TOTPDevice, user=request.user)
        code = request.POST.get("code", "")
        if verify_totp(device.secret, code):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            messages.success(request, "Autenticação em duas etapas ativada!")
            return redirect("accounts:totp_status")
        messages.error(request, "Código inválido — confira o app autenticador e tente de novo.")
        return render(request, self.template_name, self._context(device, request.user))

    @staticmethod
    def _context(device, user):
        uri = otpauth_uri(secret=device.secret, username=user.username)
        return {"secret": device.secret, "qr_data_uri": qr_data_uri(uri)}


class TOTPDisableView(LoginRequiredMixin, View):
    def post(self, request):
        TOTPDevice.objects.filter(user=request.user).delete()
        messages.success(request, "Autenticação em duas etapas desativada.")
        return redirect("accounts:totp_status")
