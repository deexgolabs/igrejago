import pytest

from linkbio.models import BioPage, Link


@pytest.fixture
def bio_page(db, church):
    return BioPage.objects.create(church=church, slug="links", church_name="Igreja Teste")


@pytest.mark.django_db
class TestPublicBioPage:
    def test_public_page_lists_only_active_links(self, client, bio_page):
        Link.objects.create(church=bio_page.church, page=bio_page, title="Ativo", url="https://a.com", order=1, is_active=True)
        Link.objects.create(church=bio_page.church, page=bio_page, title="Inativo", url="https://b.com", order=2, is_active=False)

        response = client.get(f"/{bio_page.church.slug}/links/links/")
        assert response.status_code == 200
        assert b"Ativo" in response.content
        assert b"Inativo" not in response.content

    def test_inactive_page_returns_404(self, client, bio_page):
        bio_page.is_active = False
        bio_page.save()
        response = client.get(f"/{bio_page.church.slug}/links/links/")
        assert response.status_code == 404

    def test_page_links_to_tracking_redirect_not_raw_url(self, client, bio_page):
        Link.objects.create(church=bio_page.church, page=bio_page, title="Site", url="https://exemplo.com", order=1)
        response = client.get(f"/{bio_page.church.slug}/links/links/")
        assert b"https://exemplo.com" not in response.content
        assert b"/links/click/" in response.content


@pytest.mark.django_db
class TestLinkClickTracking:
    def test_click_increments_counter_and_redirects(self, client, bio_page):
        link = Link.objects.create(
            church=bio_page.church, page=bio_page, title="Site", url="https://exemplo.com", order=1, click_count=0
        )
        response = client.get(f"/{bio_page.church.slug}/links/click/{link.pk}/")
        assert response.status_code == 302
        assert response.url == "https://exemplo.com"
        link.refresh_from_db()
        assert link.click_count == 1

    def test_inactive_link_click_returns_404(self, client, bio_page):
        link = Link.objects.create(
            church=bio_page.church, page=bio_page, title="Site", url="https://exemplo.com", is_active=False
        )
        response = client.get(f"/{bio_page.church.slug}/links/click/{link.pk}/")
        assert response.status_code == 404


@pytest.mark.django_db
class TestLinkReordering:
    def test_move_up_swaps_order_with_previous_link(self, pastor_client, bio_page):
        first = Link.objects.create(church=bio_page.church, page=bio_page, title="Primeiro", url="https://a.com", order=10)
        second = Link.objects.create(church=bio_page.church, page=bio_page, title="Segundo", url="https://b.com", order=20)

        pastor_client.post(f"/links/admin/links/{second.pk}/mover/up/")

        first.refresh_from_db()
        second.refresh_from_db()
        assert second.order < first.order

    def test_move_up_on_first_link_is_a_noop(self, pastor_client, bio_page):
        first = Link.objects.create(church=bio_page.church, page=bio_page, title="Primeiro", url="https://a.com", order=10)
        pastor_client.post(f"/links/admin/links/{first.pk}/mover/up/")
        first.refresh_from_db()
        assert first.order == 10

    def test_member_cannot_manage_links(self, member_client):
        response = member_client.get("/links/admin/")
        assert response.status_code == 403
