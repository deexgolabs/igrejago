"""Ajuda compartilhada pra LGPD (Fase 3) — usado nos 3 pontos de coleta
pública que precisam da mesma checkbox de consentimento com link pra
política de privacidade: `people.PublicVisitorForm`,
`events.PublicRegistrationForm` e `custom_forms.PublicFormView` (esse
último não usa `django.forms`, monta o campo na mão)."""

from django.urls import reverse
from django.utils.html import format_html


def privacy_consent_label():
    # Função, não constante de módulo: `reverse()` não pode rodar na hora
    # que ESTE módulo é importado — os módulos de formulário que chamam
    # isso são importados cedo demais (no meio da própria montagem do
    # URLconf), antes da rota `core:privacy_policy` existir de verdade
    # pro resolver. Chamado de dentro de `__init__`/`get()` (depois que
    # tudo já carregou), fica fora desse risco.
    return format_html(
        'Li e concordo com a <a href="{}" target="_blank" class="underline">Política de Privacidade</a>',
        reverse("core:privacy_policy"),
    )
