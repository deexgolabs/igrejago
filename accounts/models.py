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
        # membros comuns só enxergam os próprios dados.
        return self.role in (self.Role.PASTOR, self.Role.ADMIN, self.Role.LEADER)

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
