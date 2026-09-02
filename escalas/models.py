import uuid

from django.db import models

from core.tenancy import TenantModel
from people.models import Department


class Escala(TenantModel):
    """Uma escala de serviço — um departamento/ministério, numa data
    específica (ex.: "Louvor, culto de domingo de manhã, 07/09")."""

    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="escalas", verbose_name="Ministério",
    )
    date = models.DateField("Data")
    time = models.TimeField("Horário", null=True, blank=True)
    title = models.CharField("Título", max_length=200, blank=True)
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Escala"
        verbose_name_plural = "Escalas"
        ordering = ["date", "time"]

    def __str__(self):
        return f"{self.department.name} — {self.date:%d/%m/%Y}"


class EscalaVoluntario(TenantModel):
    """Um voluntário escalado numa `Escala` — status de confirmação
    próprio, com um link público (por token) pra confirmar ou recusar
    sem precisar de login (mesmo espírito de `Registration.checkin_token`)."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando confirmação"
        CONFIRMED = "CONFIRMED", "Confirmado"
        DECLINED = "DECLINED", "Não vai poder ir"

    escala = models.ForeignKey(Escala, on_delete=models.CASCADE, related_name="voluntarios", verbose_name="Escala")
    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="escalas_voluntario", verbose_name="Voluntário",
    )
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    confirm_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    confirmed_at = models.DateTimeField("Respondido em", null=True, blank=True)
    role = models.CharField(
        "Função nesse culto", max_length=100, blank=True,
        help_text="Opcional — ex.: Vocal, Baixo, Bateria, Teclado, Recepção.",
    )

    class Meta:
        verbose_name = "Voluntário escalado"
        verbose_name_plural = "Voluntários escalados"
        ordering = ["escala__date"]
        constraints = [
            models.UniqueConstraint(fields=["escala", "person"], name="unique_person_per_escala"),
        ]

    def __str__(self):
        return f"{self.person.full_name} — {self.escala}"


class IndisponibilidadeVoluntario(TenantModel):
    """Uma data em que a pessoa avisou que NÃO pode servir — usado por
    `gerar_escalas_mensais` pra pular ela no rodízio naquele domingo
    específico (ver o comando). Sem `department`: a indisponibilidade é
    da PESSOA numa data, independente de qual departamento ela serve."""

    person = models.ForeignKey(
        "people.Person", on_delete=models.CASCADE, related_name="indisponibilidades", verbose_name="Pessoa",
    )
    date = models.DateField("Data")
    motivo = models.CharField("Motivo", max_length=200, blank=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Indisponibilidade"
        verbose_name_plural = "Indisponibilidades"
        ordering = ["date"]
        constraints = [
            models.UniqueConstraint(fields=["person", "date"], name="unique_indisponibilidade_por_dia"),
        ]

    def __str__(self):
        return f"{self.person.full_name} — {self.date:%d/%m/%Y}"


class TrocaEscala(TenantModel):
    """Pedido de troca de escala, self-service — o voluntário confirmado
    pede pra repassar o compromisso, os colegas do mesmo departamento
    recebem o link público (`token`, mesmo espírito de
    `EscalaVoluntario.confirm_token`) e o primeiro que aceitar assume o
    lugar. A secretaria/líder só acompanha, não precisa agir."""

    class Status(models.TextChoices):
        PENDING = "PENDING", "Aguardando alguém aceitar"
        ACEITA = "ACEITA", "Aceita"
        CANCELADA = "CANCELADA", "Cancelada"

    escala_voluntario = models.ForeignKey(
        EscalaVoluntario, on_delete=models.CASCADE, related_name="trocas", verbose_name="Escalado",
    )
    status = models.CharField("Status", max_length=10, choices=Status.choices, default=Status.PENDING)
    aceito_por = models.ForeignKey(
        "people.Person", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trocas_aceitas", verbose_name="Aceita por",
    )
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)
    resolved_at = models.DateTimeField("Resolvida em", null=True, blank=True)

    class Meta:
        verbose_name = "Troca de escala"
        verbose_name_plural = "Trocas de escala"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Troca — {self.escala_voluntario} ({self.get_status_display()})"


class Song(TenantModel):
    """Uma música do repertório da igreja (não por departamento — é a
    biblioteca inteira, reaproveitada em qualquer `Escala` via
    `EscalaSong`). `chord_chart` aceita PDF ou imagem da cifra; `lyrics`
    é o texto puro (letra+cifra) pra quem prefere ler direto na tela."""

    title = models.CharField("Título", max_length=200)
    artist = models.CharField("Artista/banda", max_length=150, blank=True)
    default_key = models.CharField("Tom padrão", max_length=10, blank=True)
    chord_chart = models.FileField(
        "Cifra (PDF ou imagem)", upload_to="songs/cifras/%Y/%m/", blank=True, null=True,
    )
    lyrics = models.TextField("Letra/cifra em texto", blank=True)
    tags = models.CharField("Tags", max_length=200, blank=True, help_text="Separadas por vírgula.")
    created_at = models.DateTimeField("Criada em", auto_now_add=True)

    class Meta:
        verbose_name = "Música"
        verbose_name_plural = "Músicas"
        ordering = ["title"]

    def __str__(self):
        return f"{self.title} — {self.artist}" if self.artist else self.title


class EscalaSong(TenantModel):
    """Uma música do repertório escalada pra um culto específico — o tom
    (`key`) é o escolhido PRA ESSE culto, podendo diferir do
    `Song.default_key` (a mesma música pode ser tocada em tons
    diferentes dependendo de quem está cantando)."""

    escala = models.ForeignKey(Escala, on_delete=models.CASCADE, related_name="songs", verbose_name="Escala")
    song = models.ForeignKey(Song, on_delete=models.CASCADE, related_name="escalas", verbose_name="Música")
    key = models.CharField("Tom nesse culto", max_length=10, blank=True)
    order = models.PositiveIntegerField("Ordem", default=0)

    class Meta:
        verbose_name = "Música escalada"
        verbose_name_plural = "Músicas escaladas"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.song.title} — {self.escala}"


class ServiceOrderItem(TenantModel):
    """Um item da ordem do culto (ex.: "Abertura", "Louvor", "Palavra",
    "Ceia") — genérico o bastante pra qualquer `Escala` estruturada, não
    só as de louvor."""

    escala = models.ForeignKey(
        Escala, on_delete=models.CASCADE, related_name="ordem_culto", verbose_name="Escala",
    )
    order = models.PositiveIntegerField("Ordem", default=0)
    title = models.CharField("Item", max_length=150)
    duration_minutes = models.PositiveIntegerField("Duração (min)", null=True, blank=True)
    notes = models.CharField("Observações", max_length=255, blank=True)

    class Meta:
        verbose_name = "Item da ordem do culto"
        verbose_name_plural = "Itens da ordem do culto"
        ordering = ["order", "id"]

    def __str__(self):
        return f"{self.title} — {self.escala}"
