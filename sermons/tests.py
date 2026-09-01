import pytest

from sermons.models import Sermon


@pytest.mark.django_db
class TestSermonCRUD:
    def test_create_redirects_to_list(self, pastor_client):
        response = pastor_client.post("/sermoes/novo/", {
            "title": "A Parábola do Semeador",
            "preacher_name": "Pr. João",
            "date": "2026-01-05",
            "series": "Parábolas",
            "description": "",
            "youtube_url": "",
            "external_video_url": "",
            "is_published": "on",
        })
        assert response.status_code == 302
        assert response.url == "/sermoes/"
        sermon = Sermon.objects.get(title="A Parábola do Semeador")
        assert sermon.is_published is True

    def test_member_cannot_create_sermon(self, member_client):
        response = member_client.get("/sermoes/novo/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestSermonYoutubeEmbed:
    @pytest.mark.parametrize("url", [
        "https://www.youtube.com/watch?v=abc123XYZ",
        "https://youtu.be/abc123XYZ",
        "https://www.youtube.com/embed/abc123XYZ",
    ])
    def test_extracts_video_id_from_common_url_formats(self, church, url):
        sermon = Sermon.objects.create(church=church, title="Culto", date="2026-01-05", youtube_url=url)
        assert sermon.youtube_embed_url == "https://www.youtube.com/embed/abc123XYZ"

    def test_blank_when_no_youtube_url(self, church):
        sermon = Sermon.objects.create(church=church, title="Culto", date="2026-01-05")
        assert sermon.youtube_embed_url == ""


@pytest.mark.django_db
class TestSermonPublicList:
    def test_lists_only_published_sermons_from_the_right_church(self, client, church, outra_church):
        Sermon.objects.create(church=church, title="Publicado", date="2026-01-05", is_published=True)
        Sermon.objects.create(church=church, title="Rascunho", date="2026-01-05", is_published=False)
        Sermon.objects.create(church=outra_church, title="De outra igreja", date="2026-01-05", is_published=True)

        response = client.get(f"/{church.slug}/sermoes/")
        assert response.status_code == 200
        titles = [s.title for s in response.context["sermons"]]
        assert titles == ["Publicado"]
