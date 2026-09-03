"""Autenticação da API de leitura — por CHAVE, não por login/sessão
(é feita pra ser chamada por outro sistema, não por um navegador
logado). Mesmo espírito de `core.tenancy.PublicChurchMixin` (a igreja
não vem de `request.user`, vem de outro lugar — aqui, do cabeçalho
`Authorization`), mas resolvendo por `Church.api_key` em vez de slug na
URL."""

from django.http import JsonResponse

from core.models import Church
from core.ratelimit import RateLimitMixin
from core.tenant_context import tenant_context


class ApiKeyRequiredMixin:
    """`Authorization: Bearer <chave>` — sem isso, ou com uma chave que
    não bate com nenhuma igreja, 401. Resolve `request.church` e o
    thread-local do tenant a partir da chave, igual `PublicChurchMixin`
    faz a partir do slug."""

    def dispatch(self, request, *args, **kwargs):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        key = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
        if not key:
            return JsonResponse({"detail": "Cabeçalho Authorization: Bearer <chave> obrigatório."}, status=401)

        church = Church.objects.filter(api_key=key).first()
        if church is None:
            return JsonResponse({"detail": "Chave de API inválida."}, status=401)

        request.church = church
        with tenant_context(church):
            return super().dispatch(request, *args, **kwargs)


class ApiKeyRateLimitMixin(RateLimitMixin):
    """Limita por CHAVE de API (não por IP — várias igrejas podem estar
    atrás do mesmo NAT/proxy corporativo e não devem dividir o mesmo
    limite). Conta GET, POST e PATCH — achado numa revisão de
    segurança: só GET era contado, então os endpoints de escrita
    (criar/editar Pessoa, lançar Transação, inscrever em Evento,
    adicionados numa rodada depois desta classe existir) ficavam sem
    nenhum limite pra uma chave vazada."""

    rate_limit_methods = ("GET", "POST", "PATCH")
    rate_limit_max = 300
    rate_limit_window_seconds = 300

    def _rate_limit_identity(self, request):
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        return auth_header.removeprefix("Bearer ").strip() or "sem-chave"


def paginate(request, queryset, serialize, *, default_page_size=25, max_page_size=100):
    """Paginação simples e explícita (sem Paginator genérico do Django,
    que devolve objetos pensados pra template, não JSON) — `serialize` é
    uma função `obj -> dict`, chamada só nos itens da página atual."""
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(max_page_size, max(1, int(request.GET.get("page_size", default_page_size))))
    except ValueError:
        page_size = default_page_size

    total = queryset.count()
    start = (page - 1) * page_size
    items = [serialize(obj) for obj in queryset[start:start + page_size]]
    return {
        "results": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "has_next": start + page_size < total,
    }
