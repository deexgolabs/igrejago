from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class CanManagePeopleMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restringe a view a Pastor/Admin/Líder — quem pode gerenciar membros
    e visitantes (ver `accounts.User.can_manage_people`). Também exige
    `request.church` (multi-tenência: um usuário sem igreja é o dono da
    plataforma, que não gerencia pessoas de igreja nenhuma por aqui — só
    pelo Django admin, cross-tenant)."""

    def test_func(self):
        return self.request.user.can_manage_people and self.request.church is not None
