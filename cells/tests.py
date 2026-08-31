import pytest

from cells.models import Cell


@pytest.mark.django_db
class TestCellCRUD:
    def test_create_redirects_to_detail_without_crashing(self, pastor_client, person):
        """Regressão: Cell não tinha `get_absolute_url()` nem a view
        `success_url`, então criar uma célula quebrava com
        ImproperlyConfigured no redirect pós-save."""
        response = pastor_client.post("/celulas/novo/", {
            "name": "Célula Teste",
            "leader": person.pk,
            "members": [person.pk],
            "meeting_time": "19:30",
        })
        assert response.status_code == 302
        cell = Cell.objects.get(name="Célula Teste")
        assert response.url == f"/celulas/{cell.pk}/"

    def test_member_cannot_create_cell(self, member_client):
        response = member_client.get("/celulas/novo/")
        assert response.status_code == 403


@pytest.mark.django_db
class TestCellMeetingAttendance:
    def test_total_present_sums_attendees_and_visitors(self, pastor_client, person, church):
        cell = Cell.objects.create(church=church, name="Célula X")
        cell.members.add(person)

        response = pastor_client.post(f"/celulas/{cell.pk}/reuniao/nova/", {
            "date": "2026-01-05",
            "attendees": [person.pk],
            "visitors_count": "3",
            "notes": "",
        })
        assert response.status_code == 302
        meeting = cell.meetings.get()
        assert meeting.attendees.count() == 1
        assert meeting.total_present == 4
