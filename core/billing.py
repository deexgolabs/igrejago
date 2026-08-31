"""Planos e limites (Fase 4) — dict fixo no código, não um model novo
(decisão do usuário: 2 planos fixos por enquanto, ajuste de preço/limite
é redeploy, não tela de admin). Durante o `trial`, toda igreja tem
acesso completo (sem limite de pessoas, WhatsApp liberado) — os limites
abaixo só valem depois que `status` vira `ativo` com um `plano`
escolhido; `suspenso` já bloqueia o uso inteiro via
`core.middleware.TenantMiddleware`, então nem chega a importar aqui."""

from core.models import Church

PLANOS = {
    "basico": {
        "nome": "Básico",
        "preco": 49,
        "limite_pessoas": 100,
        "whatsapp": False,
    },
    "pro": {
        "nome": "Pro",
        "preco": 99,
        "limite_pessoas": None,
        "whatsapp": True,
    },
}


def plano_info(church):
    return PLANOS.get(church.plano)


def pode_adicionar_pessoa(church):
    """`True` sem limite durante o trial ou pra quem não tem plano ainda
    reconhecido (evita travar quem está em transição/dados inconsistentes
    — melhor deixar passar do que bloquear a igreja sem motivo claro)."""
    if church.status != Church.Status.ACTIVE:
        return True
    info = plano_info(church)
    if info is None or info["limite_pessoas"] is None:
        return True
    from people.models import Person

    return Person.objects.count() < info["limite_pessoas"]


def whatsapp_liberado(church):
    if church.status != Church.Status.ACTIVE:
        return True
    info = plano_info(church)
    return bool(info and info["whatsapp"])
