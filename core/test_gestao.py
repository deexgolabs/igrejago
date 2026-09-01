"""Gestão da plataforma — área de Super Admin pro dono da plataforma
(`User.is_platform_owner`, `accounts.mixins.IsPlatformOwnerMixin`). Ver
plano em `.claude/plans/quiet-enchanting-seahorse.md`."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest

from core.models import Church
from people.models import Person


@pytest.mark.django_db
class TestGestaoAccessControl:
    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get("/gestao/")
        assert response.status_code == 302
        assert "/accounts/login" in response.url

    def test_pastor_cannot_access(self, pastor_client):
        assert pastor_client.get("/gestao/").status_code == 403
        assert pastor_client.get("/gestao/igrejas/").status_code == 403
        assert pastor_client.get("/gestao/comandos/").status_code == 403

    def test_member_cannot_access(self, member_client):
        assert member_client.get("/gestao/").status_code == 403

    def test_platform_owner_can_access(self, platform_owner_client):
        assert platform_owner_client.get("/gestao/").status_code == 200
        assert platform_owner_client.get("/gestao/igrejas/").status_code == 200
        assert platform_owner_client.get("/gestao/comandos/").status_code == 200

    def test_platform_owner_hitting_root_dashboard_is_redirected_to_gestao(self, platform_owner_client):
        response = platform_owner_client.get("/")
        assert response.status_code == 302
        assert response.url == "/gestao/"


@pytest.mark.django_db
class TestGestaoDashboard:
    def test_counts_by_status(self, platform_owner_client, church, outra_church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO
        church.save()
        outra_church.status = Church.Status.SUSPENDED
        outra_church.save()

        response = platform_owner_client.get("/gestao/")
        by_status = {row["status"]: row["total"] for row in response.context["churches_by_status"]}
        assert by_status.get(Church.Status.ACTIVE) == 1
        assert by_status.get(Church.Status.SUSPENDED) == 1

    def test_mrr_sums_only_active_recognized_plans(self, platform_owner_client, church, outra_church):
        church.status = Church.Status.ACTIVE
        church.plano = Church.Plano.PRO  # R$99
        church.save()
        outra_church.status = Church.Status.TRIAL  # não entra na conta
        outra_church.save()

        response = platform_owner_client.get("/gestao/")
        assert response.context["mrr_estimado"] == 99

    def test_trials_expiring_soon_excludes_far_future(self, platform_owner_client, church, outra_church):
        church.status = Church.Status.TRIAL
        church.trial_expira_em = date.today() + timedelta(days=5)
        church.save()
        outra_church.status = Church.Status.TRIAL
        outra_church.trial_expira_em = date.today() + timedelta(days=60)
        outra_church.save()

        response = platform_owner_client.get("/gestao/")
        expiring = list(response.context["trials_expiring_soon"])
        assert church in expiring
        assert outra_church not in expiring


@pytest.mark.django_db
class TestGestaoChurchList:
    def test_filter_by_status(self, platform_owner_client, church, outra_church):
        church.status = Church.Status.ACTIVE
        church.save()
        outra_church.status = Church.Status.TRIAL
        outra_church.save()

        response = platform_owner_client.get("/gestao/igrejas/", {"status": Church.Status.ACTIVE})
        churches = list(response.context["churches"])
        assert church in churches
        assert outra_church not in churches

    def test_search_by_name(self, platform_owner_client, church, outra_church):
        response = platform_owner_client.get("/gestao/igrejas/", {"q": church.name})
        churches = list(response.context["churches"])
        assert church in churches
        assert outra_church not in churches


@pytest.mark.django_db
class TestGestaoChurchDetail:
    def test_stats_are_scoped_to_the_right_church(self, platform_owner_client, church, outra_church):
        Person.objects.create(church=church, full_name="Da igreja certa")
        Person.objects.create(church=outra_church, full_name="Da outra igreja")

        response = platform_owner_client.get(f"/gestao/igrejas/{church.pk}/")
        assert response.status_code == 200
        assert response.context["total_pessoas"] == 1

    def test_valid_post_updates_church(self, platform_owner_client, church):
        response = platform_owner_client.post(f"/gestao/igrejas/{church.pk}/", {
            "status": Church.Status.ACTIVE,
            "plano": Church.Plano.PRO,
            "trial_expira_em": "",
        })
        assert response.status_code == 302
        church.refresh_from_db()
        assert church.status == Church.Status.ACTIVE
        assert church.plano == Church.Plano.PRO

    def test_invalid_post_does_not_change_church(self, platform_owner_client, church):
        original_status = church.status
        response = platform_owner_client.post(f"/gestao/igrejas/{church.pk}/", {
            "status": "algo-invalido", "plano": "", "trial_expira_em": "",
        })
        assert response.status_code == 200
        church.refresh_from_db()
        assert church.status == original_status


@pytest.mark.django_db
class TestGestaoCommands:
    def test_runs_command_and_stores_output_in_session(self, platform_owner_client, church):
        church.status = Church.Status.TRIAL
        church.trial_expira_em = date.today() - timedelta(days=1)
        church.save()

        response = platform_owner_client.post("/gestao/comandos/expirar_trials/executar/")
        assert response.status_code == 302
        church.refresh_from_db()
        assert church.status == Church.Status.SUSPENDED

        page = platform_owner_client.get("/gestao/comandos/")
        assert page.context["last_run"]["ok"] is True
        assert page.context["last_run"]["command"] == "expirar_trials"

    def test_unknown_command_is_404(self, platform_owner_client):
        response = platform_owner_client.post("/gestao/comandos/nao-existe/executar/")
        assert response.status_code == 404

    def test_exception_is_caught_and_marked_not_ok(self, platform_owner_client):
        with patch("core.views.call_command", side_effect=Exception("boom")):
            response = platform_owner_client.post("/gestao/comandos/expirar_trials/executar/")
        assert response.status_code == 302
        page = platform_owner_client.get("/gestao/comandos/")
        assert page.context["last_run"]["ok"] is False
