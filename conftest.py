from datetime import date

import pytest

from accounts.models import User
from core.models import Church
from people.models import Person


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.fixture
def church(db):
    # `email_confirmed=True` — a maioria dos testes assume uma igreja já
    # totalmente onboarded (ver Fase 2: sem isso, o envio de WhatsApp
    # fica bloqueado e boa parte da suíte de `notifications` quebraria
    # por um motivo alheio ao que cada teste realmente verifica). O
    # próprio fluxo de confirmação de e-mail tem seus testes dedicados
    # em `core/tests.py`, com uma igreja criada `email_confirmed=False`
    # de propósito.
    #
    # `status=TRIAL` — não o default do model (`ACTIVE`) — de propósito
    # (Fase 4): trial dá acesso completo (sem limite de pessoas, WhatsApp
    # liberado, ver `core.billing`); `ACTIVE` sem um `plano` reconhecido
    # é um estado que só existiria por engano no produto real (só vira
    # `ativo` quando o webhook do Mercado Pago confirma um plano
    # específico) e bloquearia WhatsApp pra praticamente toda a suíte por
    # um motivo alheio ao que cada teste verifica. Os testes de limite de
    # plano em si criam sua PRÓPRIA igreja `ativo`+`plano` explícito.
    return Church.objects.create(name="Igreja Teste", email_confirmed=True, status=Church.Status.TRIAL)


@pytest.fixture
def outra_church(db):
    """Uma SEGUNDA igreja — só para os testes de isolamento entre
    tenants (nada criado com `church` pode aparecer pra quem está
    logado como `outra_church`, e vice-versa)."""
    return Church.objects.create(name="Outra Igreja", email_confirmed=True, status=Church.Status.TRIAL)


@pytest.fixture
def church_config(church):
    """Alias de `church` — nome mantido por compatibilidade com os
    testes escritos antes da multi-tenência (`ChurchConfig` era
    singleton; virou `Church`, uma linha por igreja)."""
    return church


@pytest.fixture
def pastor_user(db, church):
    return User.objects.create_user(
        username="pastor", password="teste12345", role=User.Role.PASTOR, church=church
    )


@pytest.fixture
def member_user(db, church):
    return User.objects.create_user(
        username="membro", password="teste12345", role=User.Role.MEMBER, church=church
    )


@pytest.fixture
def platform_owner_user(db):
    # `church=None` de propósito — é exatamente essa condição que define
    # o dono da plataforma (ver `accounts.User.is_platform_owner`).
    return User.objects.create_user(username="dono-plataforma", password="teste12345", church=None)


@pytest.fixture
def pastor_client(client, pastor_user):
    client.force_login(pastor_user)
    return client


@pytest.fixture
def platform_owner_client(client, platform_owner_user):
    client.force_login(platform_owner_user)
    return client


@pytest.fixture
def member_client(client, member_user):
    client.force_login(member_user)
    return client


@pytest.fixture
def person(db, church):
    return Person.objects.create(
        church=church,
        full_name="Maria Souza",
        phone="62999998888",
        birth_date=date(1990, 3, 15),
        is_member=True,
        status=Person.Status.ACTIVE,
        role=Person.Role.MEMBER,
    )
