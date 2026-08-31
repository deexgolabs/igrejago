"""Testes de isolamento entre igrejas (multi-tenência, Fase 1) —
verificação #3/#4/#5 do plano aprovado: uma igreja nunca deve ver, listar
ou acessar diretamente (nem por URL adivinhada) dado de outra igreja,
tanto em telas de gestão (logado) quanto em páginas públicas (por slug).

Usa as fixtures `church`/`outra_church`/`pastor_client` de `conftest.py` —
`pastor_client` está sempre logado como usuário da igreja `church`; tudo
criado com `outra_church` deve ser inacessível/invisível pra ele."""

from datetime import date, timedelta

import pytest
from django.utils import timezone

from custom_forms.models import CustomForm
from events.models import Event
from finance.models import Transaction
from people.models import Person


@pytest.mark.django_db
class TestPersonIsolation:
    def test_list_shows_only_own_church_people(self, pastor_client, church, outra_church):
        Person.objects.create(church=church, full_name="Da Igreja A")
        Person.objects.create(church=outra_church, full_name="Da Igreja B")

        response = pastor_client.get("/pessoas/")
        assert b"Da Igreja A" in response.content
        assert b"Da Igreja B" not in response.content

    def test_direct_access_to_other_church_person_is_404_not_403(self, pastor_client, outra_church):
        pessoa_b = Person.objects.create(church=outra_church, full_name="Da Igreja B")
        response = pastor_client.get(f"/pessoas/{pessoa_b.pk}/")
        # 404, não 403 — não confirma pro atacante que o ID existe.
        assert response.status_code == 404


@pytest.mark.django_db
class TestTransactionIsolation:
    def test_totals_ignore_other_church_transactions(self, pastor_client, church, outra_church):
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=100, date=date.today())
        Transaction.objects.create(church=outra_church, type="INCOME", category="TITHE", amount=999, date=date.today())

        response = pastor_client.get("/financeiro/")
        assert response.context["total_income"] == 100

    def test_direct_access_to_other_church_transaction_is_404(self, pastor_client, outra_church):
        transacao_b = Transaction.objects.create(
            church=outra_church, type="INCOME", category="TITHE", amount=999, date=date.today()
        )
        response = pastor_client.get(f"/financeiro/{transacao_b.pk}/editar/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestCustomFormIsolation:
    def test_list_shows_only_own_church_forms(self, pastor_client, church, outra_church):
        CustomForm.objects.create(church=church, title="Da Igreja A")
        CustomForm.objects.create(church=outra_church, title="Da Igreja B")

        response = pastor_client.get("/formularios/")
        assert b"Da Igreja A" in response.content
        assert b"Da Igreja B" not in response.content

    def test_direct_access_to_other_church_form_is_404(self, pastor_client, outra_church):
        formulario_b = CustomForm.objects.create(church=outra_church, title="Da Igreja B")
        response = pastor_client.get(f"/formularios/{formulario_b.pk}/editar/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestPublicPageCrossTenantIsolation:
    """Página pública (sem login) criada na igreja A não pode ser
    acessada trocando só o slug da igreja na URL pela B."""

    def test_event_from_church_a_not_reachable_under_church_b_slug(self, client, church, outra_church):
        event = Event.objects.create(
            church=church, title="Culto A",
            start_datetime=timezone.now() + timedelta(days=1),
            status=Event.EventStatus.PUBLISHED,
        )
        response = client.get(f"/{outra_church.slug}/eventos/{event.slug}/")
        assert response.status_code == 404

        response = client.get(f"/{church.slug}/eventos/{event.slug}/")
        assert response.status_code == 200

    def test_custom_form_from_church_a_not_reachable_under_church_b_slug(self, client, church, outra_church):
        form = CustomForm.objects.create(church=church, title="Pesquisa A", is_active=True)
        response = client.get(f"/{outra_church.slug}/formularios/{form.slug}/")
        assert response.status_code == 404

        response = client.get(f"/{church.slug}/formularios/{form.slug}/")
        assert response.status_code == 200
