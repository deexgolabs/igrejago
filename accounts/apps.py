from django.apps import AppConfig


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        # O Django admin tem sua PRÓPRIA tela de login (`/admin/login/`),
        # separada de `accounts:login` — sem isso, alguém sem 2FA
        # configurado ainda passaria batido pelo /admin/ direto, já que o
        # 2FA só é aplicado dentro de `RateLimitedLoginView`. Redirecionar
        # o login do admin pro login do app garante um único portão de
        # entrada pro sistema inteiro (e junto com ele, o 2FA quando
        # ativado). Import local — só é seguro mexer no AdminSite depois
        # que os apps terminaram de carregar.
        from django.contrib import admin, messages
        from django.shortcuts import redirect

        def _redirect_to_app_login(request, extra_context=None):
            from accounts.models import TOTPDevice

            # Autenticado mas sem passar em `has_permission` (ver abaixo)
            # só acontece por 2FA pendente num `is_staff` — nesse caso o
            # certo é mandar pra CONFIGURAR o 2FA, não pro login de novo
            # (senão vira looping: login OK → /admin/ → nega de novo →
            # login de novo → ...). Qualquer outro caso de "autenticado mas
            # sem permissão" (não é staff mesmo) volta pro dashboard.
            if request.user.is_authenticated:
                if request.user.is_staff and not TOTPDevice.objects.filter(
                    user=request.user, confirmed=True
                ).exists():
                    messages.warning(
                        request,
                        "Configure a autenticação em duas etapas para acessar o Django admin.",
                    )
                    return redirect("accounts:totp_setup")
                return redirect("core:dashboard")

            # Duas formas de chegar aqui sem estar logado: (1) uma página
            # do admin qualquer — `AdminSite.admin_view()` chama
            # `self.login` DIRETO (sem passar por /admin/login/ antes),
            # então `request.path` já é a própria página que a pessoa
            # queria ver; (2) visita direta em /admin/login/ (ex.: link
            # salvo) — aí não faz sentido usar o próprio /admin/login/
            # como destino depois do login, então cai pro painel do admin.
            next_url = request.GET.get("next")
            if not next_url:
                next_url = request.path if request.path != "/admin/login/" else "/admin/"
            return redirect(f"/accounts/login/?next={next_url}")

        admin.site.login = _redirect_to_app_login

        # 2FA OBRIGATÓRIO pra quem é `is_staff` (o "dono" do sistema, na
        # nomenclatura deste projeto) — essa conta guarda credenciais
        # técnicas sensíveis (Evolution API) e agora também vê tudo no
        # financeiro/formulários, então não fica só "recomendado" como
        # pro resto das contas. Um `TOTPDevice` não confirmado (ninguém
        # nunca terminou de configurar) não conta — só bloquearia sem
        # nunca deixar entrar de novo.
        _original_has_permission = admin.site.has_permission

        def _has_permission_with_2fa(request):
            from accounts.models import TOTPDevice

            if not _original_has_permission(request):
                return False
            return TOTPDevice.objects.filter(user=request.user, confirmed=True).exists()

        admin.site.has_permission = _has_permission_with_2fa
