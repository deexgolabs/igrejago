"""Guarda o usuário da request atual numa variável de thread-local, pra
signals de model (que não recebem `request`) conseguirem saber quem fez a
alteração — mesmo padrão usado no crm-odonto pra auditoria."""

import threading

from core.tenant_context import clear_current_church, set_current_church

_local = threading.local()


def get_current_user():
    return getattr(_local, "user", None)


class CurrentUserMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.user = getattr(request, "user", None)
        try:
            return self.get_response(request)
        finally:
            _local.user = None


class TenantMiddleware:
    """Resolve `request.church` a partir do usuário logado
    (`user.church` — `None` pra quem não tem igreja, o "dono da
    plataforma") e põe no thread-local (`core.tenant_context`) pra
    `core.tenancy.TenantManager` filtrar `Model.objects` sozinho durante
    esta requisição. Precisa vir DEPOIS de `AuthenticationMiddleware` no
    `MIDDLEWARE` (`request.user` só existe a partir dali).

    Também bloqueia quem está logado numa igreja `esta_bloqueada`
    (trial vencido sem assinar, ou assinatura cancelada/pagamento
    falhou — Fases 2/4) redirecionando pra `core:conta_suspensa`, exceto
    numa pequena allowlist de caminhos que precisam continuar
    acessíveis (login/logout, a própria tela de assinatura/suspensa,
    admin, estáticos/mídia) — sem isso ninguém suspenso conseguiria nem
    ver POR QUE foi bloqueado, nem assinar um plano pra se desbloquear."""

    _ALLOWLIST_PREFIXES = (
        "/accounts/", "/assinatura/", "/admin/", "/static/", "/media/",
        "/conta-suspensa/", "/health/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        church = getattr(user, "church", None) if user is not None and user.is_authenticated else None
        request.church = church
        set_current_church(church)
        try:
            if church is not None and church.esta_bloqueada and not request.path.startswith(self._ALLOWLIST_PREFIXES):
                from django.shortcuts import redirect
                return redirect("core:conta_suspensa")
            return self.get_response(request)
        finally:
            clear_current_church()
