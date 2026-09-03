"""Helpers de upload compartilhados — achados numa revisão de
segurança (ver `.claude/plans/quiet-enchanting-seahorse.md`):

1. `validar_upload` — o campo tipo arquivo do formulário público
   (`custom_forms.PublicFormView`, sem login) salvava qualquer arquivo
   sem checar extensão/tamanho. Allow-list explícita + limite de
   tamanho fecha o vetor de hospedar conteúdo malicioso/esgotar disco.
2. `random_upload_to` — em produção, `/media/` é servido direto pelo
   Nginx (ver `DEPLOY.md`), sem passar pelo Django — nenhuma view
   controla quem acessa um arquivo já enviado. Um nome de arquivo
   IMPREVISÍVEL (UUID) fecha o vetor de "adivinhar a URL de mídia de
   outra igreja" pelo padrão previsível de pasta/data/nome original.
   Não é controle de acesso de verdade (quem TEM o link ainda vê) —
   isso fica documentado como próximo passo em `DEPLOY.md`."""

import uuid
from pathlib import Path

from django.utils.deconstruct import deconstructible

UPLOAD_MAX_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".jpg", ".jpeg", ".png", ".gif", ".webp",
    ".txt", ".csv",
}


def validar_upload(uploaded_file):
    """`(True, "")` se ok; `(False, "motivo")` senão — nunca levanta
    exceção, quem chama decide como mostrar o erro (mesmo padrão de
    validação "campo a campo" já usado em `PublicFormView.post`)."""
    if uploaded_file is None:
        return True, ""
    ext = Path(uploaded_file.name).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        return False, f'Tipo de arquivo não permitido ("{ext or "sem extensão"}").'
    if uploaded_file.size > UPLOAD_MAX_SIZE_BYTES:
        return False, "Arquivo maior que 10MB."
    return True, ""


@deconstructible
class _RandomUploadTo:
    """Callable `upload_to` com nome de arquivo aleatório — CLASSE (não
    uma closure comum), porque `upload_to` precisa ser serializável pra
    migração do Django gravar/reconstruir (`@deconstructible` é o jeito
    documentado do próprio Django pra isso; uma função aninhada devolvida
    por uma fábrica não é importável por nome, `makemigrations` falha
    com "Could not find function")."""

    def __init__(self, prefix):
        self.prefix = prefix

    def __call__(self, instance, filename):
        ext = Path(filename).suffix.lower()
        return f"{self.prefix}/{uuid.uuid4().hex}{ext}"


def random_upload_to(prefix):
    """Devolve um callable `upload_to` (Django aceita `(instance,
    filename) -> path`) que gera um nome aleatório, preservando só a
    extensão original."""
    return _RandomUploadTo(prefix)
