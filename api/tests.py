import pytest

from people.models import Person


@pytest.mark.django_db
class TestApiAuth:
    def test_missing_authorization_header_is_401(self, client):
        response = client.get("/api/pessoas/")
        assert response.status_code == 401

    def test_invalid_key_is_401(self, client):
        response = client.get("/api/pessoas/", HTTP_AUTHORIZATION="Bearer chave-errada")
        assert response.status_code == 401

    def test_valid_key_returns_church_scoped_data(self, client, church, outra_church):
        church.api_key = "chave-valida-123"
        church.save()
        Person.objects.create(church=church, full_name="Da Minha Igreja")
        Person.objects.create(church=outra_church, full_name="De Outra Igreja")

        response = client.get("/api/pessoas/", HTTP_AUTHORIZATION="Bearer chave-valida-123")
        assert response.status_code == 200
        data = response.json()
        names = [p["full_name"] for p in data["results"]]
        assert "Da Minha Igreja" in names
        assert "De Outra Igreja" not in names


@pytest.mark.django_db
class TestApiPagination:
    def test_page_size_is_respected(self, client, church):
        church.api_key = "chave-paginacao"
        church.save()
        for i in range(5):
            Person.objects.create(church=church, full_name=f"Pessoa {i}")

        response = client.get("/api/pessoas/?page_size=2", HTTP_AUTHORIZATION="Bearer chave-paginacao")
        data = response.json()
        assert len(data["results"]) == 2
        assert data["total"] == 5
        assert data["has_next"] is True

    def test_page_size_is_capped(self, client, church):
        church.api_key = "chave-teto"
        church.save()
        for i in range(3):
            Person.objects.create(church=church, full_name=f"Pessoa {i}")

        response = client.get("/api/pessoas/?page_size=9999", HTTP_AUTHORIZATION="Bearer chave-teto")
        assert response.json()["page_size"] == 100


@pytest.mark.django_db
class TestApiEndpoints:
    def test_transactions_only_expose_income(self, client, church):
        from finance.models import Transaction

        church.api_key = "chave-financeiro"
        church.save()
        Transaction.objects.create(church=church, type=Transaction.Type.INCOME, category=Transaction.Category.TITHE, amount=100, date="2026-03-01")
        Transaction.objects.create(church=church, type=Transaction.Type.EXPENSE, category=Transaction.Category.RENT, amount=50, date="2026-03-01")

        response = client.get("/api/doacoes/", HTTP_AUTHORIZATION="Bearer chave-financeiro")
        data = response.json()
        assert len(data["results"]) == 1
        assert data["results"][0]["category"] == "TITHE"
