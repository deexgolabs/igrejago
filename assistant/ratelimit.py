"""Rate limit de chamadas de IA por conversa — mesmo mecanismo cru de
cache do Django já usado em `core/ratelimit.py::RateLimitMixin`, só que
como função simples (o "gate" aqui não é o dispatch de uma View, é uma
chamada dentro de `assistant.engine`, disparada por webhook)."""

from django.core.cache import cache

IA_CALL_MAX = 20
IA_CALL_WINDOW_SECONDS = 3600

# Achado numa revisão de segurança: `ia_call_permitida` só protege as
# chamadas de IA — um número mandando mensagem em rajada sem nunca
# escolher "3" (fica só no menu, por exemplo) não esbarrava em
# limite nenhum, mesmo custando 1 escrita no banco + 1 envio de
# resposta por mensagem. Limite mais alto que o de IA de propósito —
# cobre TODA mensagem recebida, não só as que chamam IA.
MENSAGEM_MAX = 40
MENSAGEM_WINDOW_SECONDS = 3600


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


def mensagem_permitida(church, phone):
    """Mesmo mecanismo, mas pro VOLUME de mensagem recebida em si — não
    dá pra usar `core.ratelimit.RateLimitMixin` (por IP) aqui, porque a
    chamada sempre vem do servidor Evolution/Meta, nunca do número de
    quem manda a mensagem; tem que ser por telefone, como este módulo
    já faz. Chamada logo no início de
    `assistant.engine.processar_mensagem_recebida`, antes de qualquer
    escrita no banco."""
    cache_key = f"assistant:mensagem:{church.pk}:{phone}"
    count = cache.get(cache_key, 0)
    if count >= MENSAGEM_MAX:
        return False
    cache.set(cache_key, count + 1, MENSAGEM_WINDOW_SECONDS)
    return True
