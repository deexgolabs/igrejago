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
class TestDonationPagBank:
    """Segundo gateway — mesmo padrão de teste do Mercado Pago
    (mock de `finance.pagbank`, nunca chamada real)."""

    def test_start_without_config_shows_error(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        donation = Donation.objects.create(church=church_config, person=person, amount=50)
        response = member_client.get(f"/financeiro/doacoes/{donation.pk}/pagar/pagbank/")
        assert response.status_code == 302

    def test_start_creates_order_and_shows_pay_page(self, member_client, member_user, person, church_config):
        member_user.person = person
        member_user.save()
        church_config.pagbank_token = "token-pagbank"
        church_config.save()
        donation = Donation.objects.create(church=church_config, person=person, amount=50)

        with patch("finance.views.pagbank.criar_pedido", return_value=("ORDE_123", "https://img/qr.png", "00020126...")):
            response = member_client.get(f"/financeiro/doacoes/{donation.pk}/pagar/pagbank/")
        assert response.status_code == 200
        donation.refresh_from_db()
        assert donation.payment_reference == "ORDE_123"
        assert b"qr.png" in response.content

    def test_webhook_confirms_payment_and_creates_transaction(self, client, person, church):
        church.pagbank_token = "token-pagbank"
        church.save()
        donation = Donation.objects.create(church=church, person=person, amount=50, payment_reference="ORDE_999")

        with patch("finance.views.pagbank.consultar_pedido", return_value={"charges": [{"status": "PAID"}]}):
            response = client.post(f"/financeiro/doacoes/webhook/pagbank/?church_id={church.pk}&id=ORDE_999")
        assert response.status_code == 200
        donation.refresh_from_db()
        assert donation.status == Donation.Status.PAID
        assert Transaction.objects.filter(person=person, category=Transaction.Category.DONATION, amount=50).exists()

    def test_webhook_ignores_unpaid_order(self, client, person, church):
        church.pagbank_token = "token-pagbank"
        church.save()
        donation = Donation.objects.create(church=church, person=person, amount=50, payment_reference="ORDE_888")

        with patch("finance.views.pagbank.consultar_pedido", return_value={"charges": [{"status": "WAITING"}]}):
            client.post(f"/financeiro/doacoes/webhook/pagbank/?church_id={church.pk}&id=ORDE_888")
        donation.refresh_from_db()
        assert donation.status == Donation.Status.PENDING

    def test_webhook_missing_params_returns_400(self, client):
        response = client.post("/financeiro/doacoes/webhook/pagbank/")
        assert response.status_code == 400


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
        # Regressão real, achada em produção: sem `Cache-Control`
        # explícito, a Cloudflare (proxy na frente do site) guardava essa
        # resposta e servia o recibo de UM membro pro próximo que abrisse
        # a mesma URL — o cabeçalho é o que impede um proxy/CDN de cachear
        # um documento personalizado como esse.
        assert response["Cache-Control"] == "private, no-store"

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
        # Regressão real, achada em produção: a Cloudflare cacheou essa
        # URL (mesmo caminho pra QUALQUER pessoa logada) e serviu o
        # recibo de um membro pro próximo que abrisse o link.
        assert response["Cache-Control"] == "private, no-store"

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


@pytest.mark.django_db
class TestDRE:
    def test_groups_income_and_expense_correctly(self, church):
        from finance.dre import dre_breakdown

        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date=date(2026, 3, 10))
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.OFFERING, amount=50, date=date(2026, 3, 15))
        Transaction.objects.create(church=church, type=Transaction.Type.EXPENSE, category=Transaction.Category.RENT, amount=80, date=date(2026, 3, 5))

        breakdown = dre_breakdown(church, date(2026, 3, 1), date(2026, 3, 31))
        assert breakdown["receitas"] == 150
        assert breakdown["despesas"] == 80
        assert breakdown["resultado"] == 70

    def test_ignores_transactions_outside_period(self, church):
        from finance.dre import dre_breakdown

        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=999, date=date(2026, 1, 1))
        breakdown = dre_breakdown(church, date(2026, 3, 1), date(2026, 3, 31))
        assert breakdown["receitas"] == 0

    def test_pastor_can_view_dre(self, pastor_client, church):
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date=date.today())
        response = pastor_client.get("/financeiro/dre/")
        assert response.status_code == 200

    def test_dre_pdf_download(self, pastor_client, church):
        response = pastor_client.get("/financeiro/dre/?formato=pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert response["Cache-Control"] == "private, no-store"

    def test_member_cannot_view_dre(self, member_client):
        assert member_client.get("/financeiro/dre/").status_code == 403


@pytest.mark.django_db
class TestBalancete:
    def test_opening_balance_carries_prior_transactions(self, church):
        from finance.dre import saldo_acumulado

        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=500, date=date(2026, 1, 15))
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date=date(2026, 3, 10))
        Transaction.objects.create(church=church, type=Transaction.Type.EXPENSE, category=Transaction.Category.RENT, amount=40, date=date(2026, 3, 12))

        dados = saldo_acumulado(church, date(2026, 3, 1), date(2026, 3, 31))
        assert dados["abertura"] == 500
        assert len(dados["meses"]) == 1
        assert dados["meses"][0]["saldo_mes"] == 60
        assert dados["saldo_final"] == 560

    def test_multiple_months_accumulate(self, church):
        from finance.dre import saldo_acumulado

        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date=date(2026, 1, 5))
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=200, date=date(2026, 2, 5))

        dados = saldo_acumulado(church, date(2026, 1, 1), date(2026, 2, 28))
        assert dados["abertura"] == 0
        assert len(dados["meses"]) == 2
        assert dados["meses"][0]["saldo_acumulado"] == 100
        assert dados["meses"][1]["saldo_acumulado"] == 300

    def test_pastor_can_view_balancete_pdf(self, pastor_client, church):
        response = pastor_client.get("/financeiro/balancete/?formato=pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_member_cannot_view_balancete(self, member_client):
        assert member_client.get("/financeiro/balancete/").status_code == 403


@pytest.mark.django_db
class TestContaContabil:
    def test_pastor_can_create_account(self, pastor_client, church):
        from finance.models import ContaContabil

        response = pastor_client.post("/financeiro/plano-de-contas/novo/", {
            "code": "3.1.01", "name": "Dízimos", "tipo": "RECEITA", "is_active": "on",
        })
        assert response.status_code == 302
        conta = ContaContabil.objects.get()
        assert conta.church_id == church.pk
        assert conta.code == "3.1.01"

    def test_member_cannot_manage_chart_of_accounts(self, member_client):
        assert member_client.get("/financeiro/plano-de-contas/").status_code == 403

    def test_transaction_can_be_linked_to_account(self, pastor_client, church):
        """Desde a partida dobrada (ver TestTransactionDoubleEntryValidation),
        vincular a UMA conta exige a contrapartida também — só uma das
        duas agora é rejeitado pelo form."""
        from finance.models import ContaContabil, Transaction

        conta = ContaContabil.objects.create(church=church, code="3.1.01", name="Dízimos", tipo="RECEITA")
        caixa = ContaContabil.objects.create(church=church, code="1.1.01", name="Caixa", tipo="ATIVO")
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "150.00", "date": "2026-03-10",
            "conta_contabil": conta.pk, "conta_contrapartida": caixa.pk,
        })
        assert response.status_code == 302
        transaction = Transaction.objects.get()
        assert transaction.conta_contabil_id == conta.pk
        assert transaction.conta_contrapartida_id == caixa.pk

    def test_transaction_without_account_still_works(self, pastor_client, church):
        """`conta_contabil` é opcional — quem não monta plano de contas
        continua lançando normal, exatamente como antes desta feature."""
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "50.00", "date": "2026-03-10",
        })
        assert response.status_code == 302

    def test_deleting_account_does_not_delete_transaction(self, pastor_client, church):
        from finance.models import ContaContabil, Transaction

        conta = ContaContabil.objects.create(church=church, code="3.1.01", name="Dízimos", tipo="RECEITA")
        transaction = Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=100, date="2026-03-10", conta_contabil=conta,
        )
        pastor_client.post(f"/financeiro/plano-de-contas/{conta.pk}/excluir/")
        transaction.refresh_from_db()
        assert transaction.conta_contabil_id is None


@pytest.mark.django_db
class TestDREContabil:
    def test_groups_by_account_type(self, church):
        from finance.dre import dre_por_conta_contabil
        from finance.models import ContaContabil

        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        despesa = ContaContabil.objects.create(church=church, code="4.1", name="Aluguel", tipo="DESPESA")
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=200, date=date(2026, 3, 10), conta_contabil=receita)
        Transaction.objects.create(church=church, type="EXPENSE", category="RENT", amount=80, date=date(2026, 3, 12), conta_contabil=despesa)
        Transaction.objects.create(church=church, type="INCOME", category="OFFERING", amount=30, date=date(2026, 3, 15))  # sem conta

        breakdown = dre_por_conta_contabil(church, date(2026, 3, 1), date(2026, 3, 31))
        assert breakdown["receitas"] == 200
        assert breakdown["despesas"] == 80
        assert breakdown["resultado"] == 120
        grupo_nomes = [g["nome"] for g in breakdown["grupos"]]
        assert "Sem conta vinculada" in grupo_nomes

    def test_pastor_can_view_dre_contabil_pdf(self, pastor_client, church):
        response = pastor_client.get("/financeiro/dre-contabil/?formato=pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"


@pytest.mark.django_db
class TestContabilExport:
    def test_only_exports_transactions_with_account(self, pastor_client, church):
        from finance.models import ContaContabil

        conta = ContaContabil.objects.create(church=church, code="3.1.01", name="Dízimos", tipo="RECEITA")
        Transaction.objects.create(church=church, type="INCOME", category="TITHE", amount=100, date="2026-03-10", conta_contabil=conta)
        Transaction.objects.create(church=church, type="INCOME", category="OFFERING", amount=50, date="2026-03-11")  # sem conta

        response = pastor_client.get("/financeiro/exportar/contabil/")
        content = response.content.decode("utf-8-sig")
        rows = [line for line in content.splitlines() if line.strip()]
        assert len(rows) == 2  # cabeçalho + 1 linha (a sem conta fica de fora)
        assert "3.1.01" in content

    def test_member_cannot_export(self, member_client):
        assert member_client.get("/financeiro/exportar/contabil/").status_code == 403


@pytest.mark.django_db
class TestTransactionDoubleEntryValidation:
    def test_only_one_account_filled_is_rejected(self, pastor_client, church):
        from finance.models import ContaContabil

        conta = ContaContabil.objects.create(church=church, code="1.1.01", name="Caixa", tipo="ATIVO")
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "100.00", "date": "2026-03-10",
            "conta_contabil": conta.pk,
        })
        assert response.status_code == 200  # re-renderiza com erro
        assert not Transaction.objects.exists()

    def test_same_account_on_both_sides_is_rejected(self, pastor_client, church):
        from finance.models import ContaContabil

        conta = ContaContabil.objects.create(church=church, code="1.1.01", name="Caixa", tipo="ATIVO")
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "100.00", "date": "2026-03-10",
            "conta_contabil": conta.pk, "conta_contrapartida": conta.pk,
        })
        assert response.status_code == 200
        assert not Transaction.objects.exists()

    def test_both_accounts_filled_succeeds(self, pastor_client, church):
        from finance.models import ContaContabil

        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO")
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "100.00", "date": "2026-03-10",
            "conta_contabil": receita.pk, "conta_contrapartida": caixa.pk,
        })
        assert response.status_code == 302
        t = Transaction.objects.get()
        assert t.conta_contabil_id == receita.pk
        assert t.conta_contrapartida_id == caixa.pk

    def test_neither_account_filled_still_works(self, pastor_client, church):
        """Lançamento simples de sempre, sem nenhuma conta — continua
        funcionando igual antes desta feature."""
        response = pastor_client.post("/financeiro/novo/", {
            "type": "INCOME", "category": "TITHE", "amount": "50.00", "date": "2026-03-10",
        })
        assert response.status_code == 302


@pytest.mark.django_db
class TestSaldoDaConta:
    def test_opening_balance_plus_debit_minus_credit_for_ativo(self, church):
        from finance.balanco import saldo_da_conta
        from finance.models import ContaContabil

        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO", saldo_inicial=500)
        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        despesa = ContaContabil.objects.create(church=church, code="4.1", name="Aluguel", tipo="DESPESA")

        # Entrada: credita receita, debita caixa (dinheiro ENTRA no caixa).
        Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=200, date=date(2026, 3, 10),
            conta_contabil=receita, conta_contrapartida=caixa,
        )
        # Saída: debita despesa, credita caixa (dinheiro SAI do caixa).
        Transaction.objects.create(
            church=church, type="EXPENSE", category="RENT", amount=80, date=date(2026, 3, 12),
            conta_contabil=despesa, conta_contrapartida=caixa,
        )

        assert saldo_da_conta(caixa, date(2026, 3, 31)) == 620  # 500 + 200 - 80
        assert saldo_da_conta(receita, date(2026, 3, 31)) == 200
        assert saldo_da_conta(despesa, date(2026, 3, 31)) == 80

    def test_ignores_transactions_after_the_cutoff_date(self, church):
        from finance.balanco import saldo_da_conta
        from finance.models import ContaContabil

        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO")
        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=999, date=date(2026, 4, 1),
            conta_contabil=receita, conta_contrapartida=caixa,
        )
        assert saldo_da_conta(caixa, date(2026, 3, 31)) == 0


@pytest.mark.django_db
class TestBalancoPatrimonial:
    def test_ativo_equals_passivo_plus_pl(self, church):
        from finance.balanco import balanco_patrimonial
        from finance.models import ContaContabil

        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO", saldo_inicial=500)
        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        despesa = ContaContabil.objects.create(church=church, code="4.1", name="Aluguel", tipo="DESPESA")

        Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=200, date=date(2026, 3, 10),
            conta_contabil=receita, conta_contrapartida=caixa,
        )
        Transaction.objects.create(
            church=church, type="EXPENSE", category="RENT", amount=80, date=date(2026, 3, 12),
            conta_contabil=despesa, conta_contrapartida=caixa,
        )

        breakdown = balanco_patrimonial(church, date(2026, 3, 31))
        assert breakdown["total_ativo"] == 620
        assert breakdown["resultado_acumulado"] == 120
        assert breakdown["saldo_inicial_liquido"] == 500  # Caixa tinha R$500 antes de qualquer lançamento
        assert breakdown["total_passivo_pl"] == 620
        assert breakdown["diferenca"] == 0

    def test_pastor_can_view_pdf(self, pastor_client, church):
        response = pastor_client.get("/financeiro/balanco-patrimonial/?formato=pdf")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"

    def test_member_cannot_view(self, member_client):
        assert member_client.get("/financeiro/balanco-patrimonial/").status_code == 403


@pytest.mark.django_db
class TestLivroRazao:
    def test_running_balance_per_line(self, church):
        from finance.balanco import livro_razao
        from finance.models import ContaContabil

        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO", saldo_inicial=500)
        receita = ContaContabil.objects.create(church=church, code="3.1", name="Dízimos", tipo="RECEITA")
        despesa = ContaContabil.objects.create(church=church, code="4.1", name="Aluguel", tipo="DESPESA")

        Transaction.objects.create(
            church=church, type="INCOME", category="TITHE", amount=200, date=date(2026, 3, 10),
            conta_contabil=receita, conta_contrapartida=caixa,
        )
        Transaction.objects.create(
            church=church, type="EXPENSE", category="RENT", amount=80, date=date(2026, 3, 12),
            conta_contabil=despesa, conta_contrapartida=caixa,
        )

        dados = livro_razao(caixa, date(2026, 3, 1), date(2026, 3, 31))
        assert dados["saldo_abertura"] == 500
        assert len(dados["linhas"]) == 2
        assert dados["linhas"][0]["saldo"] == 700
        assert dados["linhas"][1]["saldo"] == 620
        assert dados["saldo_final"] == 620

    def test_pastor_can_view_with_account_selected(self, pastor_client, church):
        from finance.models import ContaContabil

        caixa = ContaContabil.objects.create(church=church, code="1.1", name="Caixa", tipo="ATIVO")
        response = pastor_client.get(f"/financeiro/livro-razao/?conta={caixa.pk}")
        assert response.status_code == 200

    def test_member_cannot_view(self, member_client):
        assert member_client.get("/financeiro/livro-razao/").status_code == 403
