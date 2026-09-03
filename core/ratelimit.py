"""Rate limit simples baseado no cache do Django (LocMemCache por padrão —
por processo; num deploy com vários workers/gunicorn cada um teria sua
própria contagem, então o limite real vira `rate_limit_max * nº de
workers`. Aceitável para o alvo deste projeto — um VPS pequeno, um
worker — mas troque por Redis (`CACHES` apontando pra um `django-redis`)
se o deploy real usar múltiplos workers e o limite precisar ser exato."""

from django.core.cache import cache
from django.http import HttpResponse


class RateLimitMixin:
    """Aplica em qualquer View baseada em classe — limita por identidade
    (IP, por padrão) e por `rate_limit_key` (pra login, cadastro público
    e inscrição em evento terem contadores independentes). Por padrão só
    conta requisições POST, que são as que importam pra brute-force/spam.

    `rate_limit_methods`/`_rate_limit_identity()`: pontos de extensão
    pra quem precisa de outro critério — ver `api.auth.ApiKeyRateLimitMixin`,
    que conta GET (a API de leitura não tem POST) e usa a CHAVE DA API em
    vez do IP (várias igrejas atrás do mesmo NAT/proxy não devem dividir
    o mesmo limite)."""

    rate_limit_key = "generic"
    rate_limit_max = 20
    rate_limit_window_seconds = 300
    rate_limit_methods = ("POST",)

    def _rate_limit_identity(self, request):
        # Achado numa revisão de segurança: atrás de um proxy reverso
        # (Nginx/Cloudflare na frente, topologia já usada em produção —
        # ver DEPLOY.md), `REMOTE_ADDR` é sempre o IP do proxy, não do
        # visitante — TODO MUNDO cai no mesmo contador, e um único
        # abusador esgota o limite pra todos os usuários legítimos.
        # `X-Forwarded-For` é uma lista "cliente, proxy1, proxy2, ..."
        # (RFC 7239-ish); o ÚLTIMO valor é o que o proxy mais próximo do
        # Django adicionou de verdade — presume um único hop de proxy
        # confiável na frente (a topologia simples deste projeto), não
        # tenta resolver múltiplos proxies encadeados nem validar que o
        # cabeçalho não foi adulterado por quem conecta direto sem
        # passar pelo proxy.
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            partes = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
            if partes:
                return partes[-1]
        return request.META.get("REMOTE_ADDR", "unknown")

    def dispatch(self, request, *args, **kwargs):
        if request.method in self.rate_limit_methods:
            identity = self._rate_limit_identity(request)
            cache_key = f"ratelimit:{self.rate_limit_key}:{identity}"
            count = cache.get(cache_key, 0)
            if count >= self.rate_limit_max:
                return HttpResponse("Muitas tentativas. Aguarde alguns minutos e tente novamente.", status=429)
            cache.set(cache_key, count + 1, self.rate_limit_window_seconds)
        return super().dispatch(request, *args, **kwargs)
