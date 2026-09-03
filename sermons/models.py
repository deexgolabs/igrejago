from django.db import models

from core.tenancy import TenantModel
from core.uploads import random_upload_to


class Sermon(TenantModel):
    """Biblioteca de sermões/mídia. Áudio é auto-hospedado (`audio_file`)
    — deliberado: o servidor (PythonAnywhere Developer) tem só 5GB de
    disco e nenhum CDN, então vídeo estouraria rápido. Quem quer vídeo
    linka pro YouTube/Instagram (`youtube_url`/`external_video_url`) em
    vez de hospedar aqui. `preacher_name` é texto livre (não FK pra
    `Person`) pra aceitar pregador convidado sem cadastro."""

    title = models.CharField("Título", max_length=200)
    preacher_name = models.CharField("Pregador", max_length=150, blank=True)
    date = models.DateField("Data")
    series = models.CharField(
        "Série", max_length=150, blank=True,
        help_text="Agrupamento livre (ex.: 'Provérbios', 'Ano Novo 2026').",
    )
    description = models.TextField("Descrição", blank=True)
    audio_file = models.FileField(
        "Arquivo de áudio", upload_to=random_upload_to("sermons/audio"), blank=True,
        help_text="MP3/M4A — tocado direto na página pública.",
    )
    youtube_url = models.URLField("Link do YouTube", blank=True)
    external_video_url = models.URLField(
        "Outro link de vídeo", blank=True,
        help_text="Instagram, Facebook etc. — mostrado como botão quando não for YouTube.",
    )
    is_published = models.BooleanField("Publicado", default=True)
    created_at = models.DateTimeField("Criado em", auto_now_add=True)

    class Meta:
        verbose_name = "Sermão"
        verbose_name_plural = "Sermões"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def youtube_embed_url(self):
        """Extrai o ID do vídeo de formatos comuns de URL do YouTube
        (`watch?v=`, `youtu.be/`, `/embed/`) e monta a URL de embed —
        usado no `<iframe>` da página pública."""
        url = self.youtube_url
        if not url:
            return ""
        video_id = ""
        if "youtu.be/" in url:
            video_id = url.split("youtu.be/")[-1].split("?")[0]
        elif "watch?v=" in url:
            video_id = url.split("watch?v=")[-1].split("&")[0]
        elif "/embed/" in url:
            video_id = url.split("/embed/")[-1].split("?")[0]
        return f"https://www.youtube.com/embed/{video_id}" if video_id else ""
