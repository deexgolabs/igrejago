"""Guarda a igreja "atual" (do request em andamento) numa variável de
thread-local — mesmo padrão já usado em `core/middleware.py` pra guardar o
usuário atual pra auditoria. É isso que `core.tenancy.TenantManager` lê
pra filtrar `Model.objects` sozinho, sem precisar passar `church=` em toda
query da aplicação.

Fora de uma requisição (management command, shell, migração de dado) não
tem nada aqui — `get_current_church()` devolve `None`, e o manager
simplesmente não filtra (mesmo comportamento de um super-admin de
plataforma sem igreja: código de servidor já é confiável). Comandos que
processam TODAS as igrejas (fila de WhatsApp, lembretes, verificação de
conexão) usam o `tenant_context()` abaixo pra processar uma igreja de
cada vez com o filtro automático funcionando igual dentro de uma
requisição normal."""

import threading
from contextlib import contextmanager

_local = threading.local()


def get_current_church():
    return getattr(_local, "church", None)


def set_current_church(church):
    _local.church = church


def clear_current_church():
    _local.church = None


@contextmanager
def tenant_context(church):
    """Roda um bloco de código com `church` como "igreja atual" — pra
    comandos de cron que processam todas as igrejas uma a uma
    (`processar_fila_whatsapp`, `enviar_lembretes`,
    `verificar_conexao_whatsapp`) reaproveitarem o código de sempre
    (`Model.objects...`, que já filtra sozinho) sem reescrever cada
    query com `todas_as_igrejas.filter(church=church, ...)` na mão."""
    set_current_church(church)
    try:
        yield church
    finally:
        clear_current_church()
