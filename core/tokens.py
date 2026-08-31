"""Token de confirmação de e-mail do cadastro público de igreja
(`core.views.ChurchSignupView`/`ConfirmEmailView`) — hand-rolled com
`django.core.signing.TimestampSigner` (sem dependência nova, mesmo
espírito das outras implementações "na mão" já feitas neste projeto:
TOTP em `accounts/totp.py`, o payload PIX em `events/pix.py`). Confirma
a IGREJA (`Church.email_confirmed`), não uma conta de usuário — mesmo
critério usado pra liberar o envio de WhatsApp por igreja."""

from django.core import signing

_SALT = "core.church_email_confirm"


def gerar_token_confirmacao(church):
    return signing.TimestampSigner(salt=_SALT).sign(str(church.pk))


def verificar_token_confirmacao(token, max_age_seconds=3 * 24 * 3600):
    """Devolve o pk da igreja se o token for válido e não tiver expirado
    (padrão: 3 dias), ou `None` caso contrário — nunca levanta exceção."""
    try:
        valor = signing.TimestampSigner(salt=_SALT).unsign(token, max_age=max_age_seconds)
        return int(valor)
    except (signing.BadSignature, ValueError):
        return None
