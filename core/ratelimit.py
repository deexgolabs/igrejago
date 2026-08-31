"""Rate limit simples baseado no cache do Django (LocMemCache por padrão —
por processo; num deploy com vários workers/gunicorn cada um teria sua
própria contagem, então o limite real vira `rate_limit_max * nº de
workers`. Aceitável para o alvo deste projeto — um VPS pequeno, um
worker — mas troque por Redis (`CACHES` apontando pra um `django-redis`)
se o deploy real usar múltiplos workers e o limite precisar ser exato."""

from django.core.cache import cache
from django.http import HttpResponse


class RateLimitMixin:
    """Aplica em qualquer View baseada em classe — limita por IP e por
    `rate_limit_key` (pra login, cadastro público e inscrição em evento
    terem contadores independentes). Só conta requisições POST, que são
    as que importam pra brute-force/spam."""

    rate_limit_key = "generic"
    rate_limit_max = 20
    rate_limit_window_seconds = 300

    def dispatch(self, request, *args, **kwargs):
        if request.method == "POST":
            ip = request.META.get("REMOTE_ADDR", "unknown")
            cache_key = f"ratelimit:{self.rate_limit_key}:{ip}"
            count = cache.get(cache_key, 0)
            if count >= self.rate_limit_max:
                return HttpResponse("Muitas tentativas. Aguarde alguns minutos e tente novamente.", status=429)
            cache.set(cache_key, count + 1, self.rate_limit_window_seconds)
        return super().dispatch(request, *args, **kwargs)
