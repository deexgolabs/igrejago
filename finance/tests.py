from datetime import date
from unittest.mock import patch

import pytest

from finance.models import Budget, Donation, RecurringPledge, Transaction


@pytest.mark.django_db
class TestTransactionTotals:
    def test_list_totals_reflect_filtered_queryset(self, pastor_client, church):
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=200, date=date.today())
        Transaction.objects.create(church=church, type="EXPENSE", category="UTILITIES", amount=80, date=date.today())

        response = pastor_client.get("/financeiro/")
        assert response.context["total_income"] == 200
        assert response.context["total_expense"] == 80
        assert response.context["balance"] == 120

    def test_type_filter_recomputes_totals(self, pastor_client, church):
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=200, date=date.today())
        Transaction.objects.create(church=church, type="EXPENSE", category="UTILITIES", amount=80, date=date.today())

        response = pastor_client.get("/financeiro/?type=EXPENSE")
        assert response.context["total_income"] == 0
        assert response.context["total_expense"] == 80


@pytest.mark.django_db
class TestTransactionPermissions:
    def test_member_cannot_access_finance(self, member_client):
        response = member_client.get("/financeiro/")
        assert response.status_code == 403

    def test_pastor_can_create_transaction(self, pastor_client):
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "OFFERING", "amount": "50.00",
            "date": date.today().isoformat(),
        })
        assert response.status_code == 302
        assert Transaction.objects.filter(category="OFFERING", amount=50).exists()


@pytest.mark.django_db
class TestTransactionExportCSV:
    def test_export_has_single_bom(self, pastor_client, church):
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=200, date=date.today())
        response = pastor_client.get("/financeiro/exportar/")
        content = response.content
        assert content[:3] == b"\xef\xbb\xbf"
        assert content.count(b"\xef\xbb\xbf") == 1


@pytest.mark.django_db
class TestBudget:
    def test_save_creates_budget_and_shows_diff(self, pastor_client, church):
        today = date.today()
        Transaction.objects.create(church=church, type="EXPENSE", category="UTILITIES", amount=80, date=today)

        response = pastor_client.post(f"/financeiro/orcamento/?month={today.year}-{today.month:02d}", {
            "month": f"{today.year}-{today.month:02d}",
            "target_UTILITIES": "100.00",
        })
        assert response.status_code == 200
        budget = Budget.objects.get(category="UTILITIES", year=today.year, month=today.month)
        assert budget.target_amount == 100

        row = next(r for r in response.context["rows"] if r["category"] == "UTILITIES")
        assert row["actual"] == 80
        assert row["diff"] == -20

    def test_blank_target_does_not_overwrite_existing_budget(self, pastor_client, church):
        today = date.today()
        Budget.objects.create(church=church, category="TITHE", year=today.year, month=today.month, target_amount=500)

        pastor_client.post(f"/financeiro/orcamento/?month={today.year}-{today.month:02d}", {
            "month": f"{today.year}-{today.month:02d}", "target_TITHE": "",
        })
        budget = Budget.objects.get(category="TITHE", year=today.year, month=today.month)
        assert budget.target_amount == 500


@pytest.mark.django_db
class TestTransactionExportExcel:
    def test_export_returns_xlsx(self, pastor_client, church):
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=200, date=date.today())
        response = pastor_client.get("/financeiro/exportar/excel/")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.mark.django_db
class TestRecurringPledge:
    def test_create_and_list(self, pastor_client, person):
        response = pastor_client.post("/financeiro/recorrentes/", {
            "person": person.pk, "monthly_amount": "100.00", "due_day": "10", "active": "on",
        })
        assert response.status_code == 302
        assert RecurringPledge.objects.filter(person=person).exists()

    def test_row_marked_em_dia_when_tithe_exists_this_month(self, pastor_client, person, church):
        RecurringPledge.objects.create(church=church, person=person, monthly_amount=100, due_day=10)
        Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=100, date=date.today(), person=person,
        )
        response = pastor_client.get("/financeiro/recorrentes/")
        row = response.context["rows"][0]
        assert row["em_dia"] is True

    def test_row_marked_em_atraso_without_transaction(self, pastor_client, person, church):
        RecurringPledge.objects.create(church=church, person=person, monthly_amount=100, due_day=10)
        response = pastor_client.get("/financeiro/recorrentes/")
        row = response.context["rows"][0]
        assert row["em_dia"] is False

    def test_toggle_active(self, pastor_client, person, church):
        pledge = RecurringPledge.objects.create(church=church, person=person, monthly_amount=100)
        pastor_client.post(f"/financeiro/recorrentes/{pledge.pk}/alternar/")
        pledge.refresh_from_db()
        assert pledge.active is False

    def test_member_cannot_access(self, member_client):
        response = member_client.get("/financeiro/recorrentes/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestDonation:
    def test_member_can_create_donation_and_see_pix(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        church_config.pix_key = "contato@igreja.org"
        church_config.pix_receiver_name = "Igreja Exemplo"
        church_config.pix_receiver_city = "GOIANIA"
        church_config.save()

        response = member_client.post("/financeiro/doacoes/nova/", {"amount": "50.00"})
        assert response.status_code == 302
        donation = Donation.objects.get(person=person)
        assert donation.amount == 50
        assert donation.status == Donation.Status.PENDING

        pay_response = member_client.get(response.url)
        assert pay_response.status_code == 200
        assert "pix_qr_data_uri" in pay_response.context

    def test_anonymous_cannot_create_donation(self, client):
        response = client.post("/financeiro/doacoes/nova/", {"amount": "50.00"})
        assert response.status_code == 302
        assert "/accounts/login/" in response.url

    def test_staff_confirming_pix_donation_creates_transaction(self, pastor_client, person, church):
        donation = Donation.objects.create(church=church, person=person, amount=75)
        response = pastor_client.post(f"/financeiro/doacoes/{donation.pk}/confirmar/")
        assert response.status_code == 302
        donation.refresh_from_db()
        assert donation.status == Donation.Status.PAID
        assert Transaction.objects.filter(
            person=person, category=Transaction.Category.DONATION, amount=75
        ).exists()

    def test_member_cannot_confirm_donation(self, member_client, person, church):
        donation = Donation.objects.create(church=church, person=person, amount=75)
        response = member_client.post(f"/financeiro/doacoes/{donation.pk}/confirmar/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestDonationReceipt:
    def test_donor_can_download_own_receipt(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        donation = Donation.objects.create(church=church_config, person=person, amount=100, status=Donation.Status.PAID)

        response = member_client.get(f"/financeiro/doacoes/{donation.pk}/recibo.pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_staff_can_download_any_receipt(self, pastor_client, person, church_config):
        donation = Donation.objects.create(church=church_config, person=person, amount=100, status=Donation.Status.PAID)
        response = pastor_client.get(f"/financeiro/doacoes/{donation.pk}/recibo.pdf")
        assert response.status_code == 200

    def test_unrelated_member_cannot_download_others_receipt(self, member_client, person, church_config):
        donation = Donation.objects.create(church=church_config, person=person, amount=100, status=Donation.Status.PAID)
        response = member_client.get(f"/financeiro/doacoes/{donation.pk}/recibo.pdf")
        assert response.status_code == 404

    def test_pending_donation_has_no_receipt_yet(self, pastor_client, person, church_config):
        donation = Donation.objects.create(church=church_config, person=person, amount=100, status=Donation.Status.PENDING)
        response = pastor_client.get(f"/financeiro/doacoes/{donation.pk}/recibo.pdf")
        assert response.status_code == 404

    def test_anonymous_cannot_download_receipt(self, client, person, church_config):
        donation = Donation.objects.create(church=church_config, person=person, amount=100, status=Donation.Status.PAID)
        response = client.get(f"/financeiro/doacoes/{donation.pk}/recibo.pdf")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url


@pytest.mark.django_db
class TestRecurringPledgeSubscribe:
    def test_member_subscribes_and_is_redirected_to_mercadopago(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        church_config.mercadopago_access_token = "fake-token"
        church_config.save()

        with patch(
            "finance.views.mercadopago.criar_assinatura_dizimo",
            return_value=("PRE123", "https://mercadopago.com/checkout/PRE123"),
        ):
            response = member_client.post("/financeiro/recorrentes/assinar/", {
                "monthly_amount": "100.00", "due_day": "10",
            })
        assert response.status_code == 302
        assert response.url == "https://mercadopago.com/checkout/PRE123"
        pledge = RecurringPledge.objects.get(person=person)
        assert pledge.mercadopago_preapproval_id == "PRE123"
        assert pledge.active is False  # só vira True quando o webhook confirmar "authorized"

    def test_api_failure_deletes_pledge_and_shows_error(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        church_config.mercadopago_access_token = "fake-token"
        church_config.save()

        with patch("finance.views.mercadopago.criar_assinatura_dizimo", side_effect=Exception("timeout")):
            response = member_client.post("/financeiro/recorrentes/assinar/", {
                "monthly_amount": "100.00", "due_day": "10",
            })
        assert response.status_code == 302
        assert not RecurringPledge.objects.filter(person=person).exists()

    def test_without_mercadopago_configured_shows_error(self, member_client, member_user, person):
        member_user.person = person
        member_user.save()
        response = member_client.post("/financeiro/recorrentes/assinar/", {
            "monthly_amount": "100.00", "due_day": "10",
        })
        assert response.status_code == 302
        assert not RecurringPledge.objects.exists()


@pytest.mark.django_db
class TestRecurringPledgeCancel:
    def test_owner_can_cancel_own_subscription(self, member_client, member_user, person, church):
        member_user.person = person
        member_user.save()
        pledge = RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, mercadopago_preapproval_id="PRE123",
        )
        with patch("finance.views.mercadopago.cancelar_assinatura", return_value={"status": "cancelled"}):
            response = member_client.post(f"/financeiro/recorrentes/{pledge.pk}/cancelar/")
        assert response.status_code == 302
        pledge.refresh_from_db()
        assert pledge.active is False
        assert pledge.mercadopago_status == "cancelled"

    def test_unrelated_member_cannot_cancel(self, member_client, person, church):
        pledge = RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, mercadopago_preapproval_id="PRE123",
        )
        response = member_client.post(f"/financeiro/recorrentes/{pledge.pk}/cancelar/")
        assert response.status_code == 404

    def test_pastor_can_cancel_anyones_subscription(self, pastor_client, person, church):
        pledge = RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, mercadopago_preapproval_id="PRE123",
        )
        with patch("finance.views.mercadopago.cancelar_assinatura", return_value={"status": "cancelled"}):
            response = pastor_client.post(f"/financeiro/recorrentes/{pledge.pk}/cancelar/")
        assert response.status_code == 302
        pledge.refresh_from_db()
        assert pledge.active is False


@pytest.mark.django_db
class TestRecurringPledgeWebhook:
    def test_missing_params_is_bad_request(self, client):
        response = client.post("/financeiro/recorrentes/webhook/mercadopago/")
        assert response.status_code == 400

    def test_preapproval_status_updates_pledge(self, client, church, person):
        church.mercadopago_access_token = "fake-token"
        church.save()
        pledge = RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, active=False, mercadopago_preapproval_id="PRE123",
        )
        with patch("finance.views.mercadopago.consultar_assinatura", return_value={"status": "authorized"}):
            response = client.post(
                f"/financeiro/recorrentes/webhook/mercadopago/?type=subscription_preapproval&id=PRE123&church_id={church.pk}"
            )
        assert response.status_code == 200
        pledge.refresh_from_db()
        assert pledge.active is True
        assert pledge.mercadopago_status == "authorized"

    def test_authorized_payment_creates_transaction(self, client, church, person):
        church.mercadopago_access_token = "fake-token"
        church.save()
        pledge = RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, active=True, mercadopago_preapproval_id="PRE123",
        )
        fake_payment = {"status": "approved", "preapproval_id": "PRE123", "transaction_amount": 100}
        with patch("finance.views.mercadopago.consultar_pagamento_autorizado", return_value=fake_payment):
            response = client.post(
                f"/financeiro/recorrentes/webhook/mercadopago/?type=subscription_authorized_payment&id=PAY1&church_id={church.pk}"
            )
        assert response.status_code == 200
        assert Transaction.objects.filter(
            person=person, category=Transaction.Category.TITHE, amount=100
        ).exists()

    def test_unapproved_payment_does_not_create_transaction(self, client, church, person):
        church.mercadopago_access_token = "fake-token"
        church.save()
        RecurringPledge.objects.create(
            church=church, person=person, monthly_amount=100, mercadopago_preapproval_id="PRE123",
        )
        fake_payment = {"status": "pending", "preapproval_id": "PRE123"}
        with patch("finance.views.mercadopago.consultar_pagamento_autorizado", return_value=fake_payment):
            response = client.post(
                f"/financeiro/recorrentes/webhook/mercadopago/?type=subscription_authorized_payment&id=PAY1&church_id={church.pk}"
            )
        assert response.status_code == 200
        assert not Transaction.objects.filter(category=Transaction.Category.TITHE).exists()


@pytest.mark.django_db
class TestAnnualDonationReceipt:
    def test_aggregates_transactions_for_the_year(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        Transaction.objects.create(
            church=church_config, type="INCOME", category="TITHE", amount=100, date=date(2026, 1, 10), person=person,
        )
        Transaction.objects.create(
            church=church_config, type="INCOME", category="OFFERING", amount=50, date=date(2026, 6, 5), person=person,
        )
        Transaction.objects.create(
            church=church_config, type="INCOME", category="TITHE", amount=999, date=date(2025, 1, 10), person=person,
        )

        response = member_client.get("/financeiro/recibo-anual.pdf?ano=2026")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")

    def test_staff_can_download_for_another_person(self, pastor_client, person, church_config):
        response = pastor_client.get(f"/financeiro/recibo-anual/{person.pk}.pdf")
        assert response.status_code == 200

    def test_unrelated_member_cannot_download_for_another_person(self, member_client, person, church_config):
        response = member_client.get(f"/financeiro/recibo-anual/{person.pk}.pdf")
        assert response.status_code == 404

    def test_anonymous_is_redirected_to_login(self, client):
        response = client.get("/financeiro/recibo-anual.pdf")
        assert response.status_code == 302
        assert "/accounts/login/" in response.url
