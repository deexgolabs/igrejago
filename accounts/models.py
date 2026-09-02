from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Usuário do sistema. O nível de acesso é controlado pelo campo `role`,
    que define o que a pessoa pode ver/fazer (RBAC simples via decorators/mixins
    nas views, sem grupos/permissões do Django por enquanto)."""

    class Role(models.TextChoices):
        PASTOR = "PASTOR", "Pastor"
        ADMIN = "ADMIN", "Secretaria/Administrador"
        LEADER = "LEADER", "Líder de Departamento"
        MEMBER = "MEMBER", "Membro"

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.MEMBER)

    # Multi-tenência: de qual igreja é esta conta. `null=True` é o
    # "dono da plataforma" (super-admin) — sem igreja, sem filtro nenhum
    # nas consultas (ver `core.tenancy.TenantManager`); toda conta comum
    # (Pastor/Admin/Líder/Membro DE UMA igreja) tem isso preenchido.
    church = models.ForeignKey(
        "core.Church",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="users",
        verbose_name="Igreja",
    )

    # Vínculo opcional com o cadastro de pessoa (preenchido quando um membro
    # ganha acesso ao sistema). Late import evita dependência circular no nível
    # de módulo — o FK real mora aqui, como string reference.
    person = models.OneToOneField(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_account",
    )

    @property
    def is_pastor(self):
        return self.role == self.Role.PASTOR

    @property
    def is_church_admin(self):
        return self.role in (self.Role.PASTOR, self.Role.ADMIN)

    @property
    def is_leader(self):
        return self.role == self.Role.LEADER

    @property
    def can_manage_people(self):
        # Pastor, secretaria e líderes de departamento gerenciam pessoas;
        # membros comuns só enxergam os próprios dados. Esse é só o
        # "portão externo" — QUANTO cada um vê é decidido por
        # `is_unrestricted_manager` mais abaixo (Líder é escopado ao
        # próprio departamento, Pastor/Admin não têm restrição nenhuma).
        return self.role in (self.Role.PASTOR, self.Role.ADMIN, self.Role.LEADER)

    @property
    def is_unrestricted_manager(self):
        # Pastor/Secretaria: acesso total, sem escopo por departamento —
        # ao contrário de um Líder (ver `is_department_leader`), que só
        # vê os recursos do(s) departamento(s) que lidera.
        return self.role in (self.Role.PASTOR, self.Role.ADMIN)

    @property
    def led_departments(self):
        # Departamento(s) que essa pessoa lidera — reaproveita o
        # `related_name="led_departments"` que `Department.leader` já
        # tinha (não duplicado num campo à parte em User, pra não ter
        # duas fontes de verdade que podem dessincronizar). Vazio pra
        # quem não tem `person` vinculado (ex.: dono da plataforma).
        from people.models import Department

        if not self.person_id:
            return Department.objects.none()
        return self.person.led_departments.all()

    @property
    def is_department_leader(self):
        # Só conta como "Líder de Departamento" escopado se: tem o role
        # certo E lidera pelo menos um Department de verdade — um usuário
        # com role=LEADER mas sem departamento nenhum atribuído não ganha
        # acesso a nada extra (fica só com o que `can_manage_people`
        # cobre, na prática nada além do próprio portal).
        return self.role == self.Role.LEADER and self.led_departments.exists()

    @property
    def led_cells(self):
        # Célula(s) que essa pessoa lidera — reaproveita o
        # `related_name="led_cells"` de `Cell.leader`. INDEPENDE de
        # `role`: um Membro comum que lidera célula já ganha acesso
        # escopado a ela (ver `accounts.mixins.CanManageCellsMixin`), sem
        # precisar virar Líder de Departamento.
        from cells.models import Cell

        if not self.person_id:
            return Cell.objects.none()
        return self.person.led_cells.filter(is_active=True)

    @property
    def is_cell_leader(self):
        return self.led_cells.exists()

    @property
    def has_checkin_access(self):
        # Mesma condição de `accounts.mixins.CheckinAccessMixin` — exposta
        # como propriedade também pra decidir se mostra o link no nav
        # (`templates/base.html`) sem duplicar a lógica no template.
        return self.is_unrestricted_manager or self.led_departments.filter(habilita_checkin=True).exists()

    @property
    def is_platform_owner(self):
        # Dono da plataforma: conta sem igreja (ver docstring do campo
        # `church` acima — `church_id is None` É a definição de "sem
        # igreja, sem filtro nenhum"). `role` não entra aqui — é um
        # conceito por igreja, irrelevante pra essa conta.
        return self.church_id is None

    def __str__(self):
        return self.get_full_name() or self.username


class TOTPDevice(models.Model):
    """Autenticação em duas etapas (TOTP) de uma conta — opcional, ninguém
    é obrigado a configurar. `confirmed=False` enquanto a pessoa ainda não
    provou (digitando um código válido) que escaneou o QR direito; só
    depois disso o login passa a exigir o segundo fator (ver
    `accounts.views.RateLimitedLoginView`)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret = models.CharField(max_length=64)
    confirmed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        status = "ativo" if self.confirmed else "pendente de confirmação"
        return f"2FA de {self.user} ({status})"
