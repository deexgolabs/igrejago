from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class CanManagePeopleMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restringe a view a Pastor/Admin/Líder — quem pode gerenciar membros
    e visitantes (ver `accounts.User.can_manage_people`). Também exige
    `request.church` (multi-tenência: um usuário sem igreja é o dono da
    plataforma, que não gerencia pessoas de igreja nenhuma por aqui — só
    pelo Django admin, cross-tenant)."""

    def test_func(self):
        return self.request.user.can_manage_people and self.request.church is not None


class IsPlatformOwnerMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restringe a view ao dono da plataforma — conta sem igreja (ver
    `accounts.User.is_platform_owner`). Ao contrário de
    `CanManagePeopleMixin`, não precisa de uma segunda condição sobre
    `request.church`: `is_platform_owner` já É `church_id is None`, então
    checar os dois seria redundante."""

    def test_func(self):
        return self.request.user.is_platform_owner


class IsChurchManagerMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restringe a view a Pastor/Secretaria (`is_unrestricted_manager`) —
    as áreas que um Líder de Departamento NÃO acessa mesmo sendo
    escopado: Eventos, Financeiro, Configurações, Departamentos,
    Formulários/Sermões (gestão), Auditoria. Diferente de
    `CanManagePeopleMixin`, que ainda deixa um Líder entrar (só que
    escopado ao próprio departamento) em Pessoas/Escalas/Check-in/
    Mensagens."""

    def test_func(self):
        return self.request.user.is_unrestricted_manager and self.request.church is not None


class CanManageCellsMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Restringe a view a quem pode mexer em Células: Pastor/Secretaria
    (tudo), Líder de Departamento (tudo, mesmo sem célula), ou um Membro
    comum que lidera a PRÓPRIA célula (`User.is_cell_leader` — não
    depende de `role`). A view em si (ver `cells/views.py`) ainda precisa
    escopar a queryset pra célula própria quando for só esse último
    caso."""

    def test_func(self):
        user = self.request.user
        if self.request.church is None:
            return False
        return user.is_unrestricted_manager or user.is_department_leader or user.is_cell_leader


class CheckinAccessMixin(CanManagePeopleMixin):
    """Check-in infantil: além do portão de `CanManagePeopleMixin`, um
    Líder de Departamento só entra se liderar um departamento com
    `habilita_checkin=True` (ex.: Ministério Infantil) — não faz sentido
    o líder do Louvor ver essa tela. Pastor/Secretaria continuam sem
    restrição. Não escopa DADOS (não tem campo de departamento em
    `Checkin`/`SalaInfantil`), só a visibilidade do módulo inteiro."""

    def test_func(self):
        return super().test_func() and self.request.user.has_checkin_access
