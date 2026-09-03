"""Rate limit de chamadas de IA por conversa — mesmo mecanismo cru de
cache do Django já usado em `core/ratelimit.py::RateLimitMixin`, só que
como função simples (o "gate" aqui não é o dispatch de uma View, é uma
chamada dentro de `assistant.engine`, disparada por webhook)."""

from django.core.cache import cache

IA_CALL_MAX = 20
IA_CALL_WINDOW_SECONDS = 3600


def ia_call_permitida(church, phone):
    """`True` e já CONTA a chamada se ainda houver saldo na janela;
    `False` sem contar nada se estourou — chamar só imediatamente antes
    de de fato usar a IA (menu opção "3" ou extração de cadastro),
    nunca no menu/eco de confirmação, que não chamam IA nenhuma."""
    cache_key = f"assistant:ia_call:{church.pk}:{phone}"
    count = cache.get(cache_key, 0)
    if count >= IA_CALL_MAX:
        return False
    cache.set(cache_key, count + 1, IA_CALL_WINDOW_SECONDS)
    return True
