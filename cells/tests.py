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


@pytest.mark.django_db
class TestCellLeaderScopedAccess:
    """`cell`/`cell_leader_client` vêm do conftest.py — um Membro comum
    (role=MEMBER) que lidera `cell` (`Cell.leader`), sem precisar virar
    Líder de Departamento (ver accounts.User.is_cell_leader)."""

    def test_cell_leader_can_access_cells_app(self, cell_leader_client):
        assert cell_leader_client.get("/celulas/").status_code == 200

    def test_cell_leader_sees_only_own_cell(self, cell_leader_client, church, cell):
        outra_celula = Cell.objects.create(church=church, name="Outra Célula")

        response = cell_leader_client.get("/celulas/")
        cells_shown = list(response.context["cells"])
        assert cells_shown == [cell]
        assert outra_celula not in cells_shown

    def test_cell_leader_cannot_open_another_cell_directly(self, cell_leader_client, church):
        outra_celula = Cell.objects.create(church=church, name="Outra Célula")
        response = cell_leader_client.get(f"/celulas/{outra_celula.pk}/")
        assert response.status_code == 404

    def test_cell_leader_can_log_own_cell_meeting(self, cell_leader_client, cell, cell_leader_person):
        cell.members.add(cell_leader_person)
        response = cell_leader_client.post(f"/celulas/{cell.pk}/reuniao/nova/", {
            "date": "2026-01-05", "attendees": [cell_leader_person.pk], "visitors_count": "0", "notes": "",
        })
        assert response.status_code == 302
        assert cell.meetings.count() == 1

    def test_department_leader_sees_all_cells_without_being_a_cell_leader(self, department_leader_client, church):
        cell_a = Cell.objects.create(church=church, name="Célula A")
        cell_b = Cell.objects.create(church=church, name="Célula B")
        response = department_leader_client.get("/celulas/")
        assert set(response.context["cells"]) == {cell_a, cell_b}

    def test_plain_member_without_a_cell_is_denied(self, member_client):
        assert member_client.get("/celulas/").status_code == 403
