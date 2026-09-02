"""
Django settings for church_crm project.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv(
    "SECRET_KEY", "django-insecure-lt&jqs@&!@fnmh3&m04o$61r36rvm(2&ctc$euupe2siv!$4@r"
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "True") == "True"

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]

# Domínios que podem submeter formulário aqui SEM bater com o Host visto
# pelo Django — necessário quando um domínio na frente (ex.: um Worker da
# Cloudflare espelhando um domínio curto pras páginas públicas) repassa a
# requisição sobrescrevendo o cabeçalho Host, mas o navegador do visitante
# ainda manda o Origin do domínio curto original. Sem isso, toda submissão
# de formulário público vinda de um domínio "espelho" desses seria barrada
# como CSRF inválido (Origin ≠ Host). Formato: URLs completas com esquema,
# separadas por vírgula (ex.: "https://igrejago.link").
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# Base do site pra montar links absolutos fora de request (ex.: comandos
# de cron como `gerar_escalas_mensais`, que não tem `request.build_absolute_uri`
# disponível) — sem barra no final.
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000")


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Terceiros
    "django_htmx",

    # Apps do projeto
    "core",
    "accounts",
    "people",
    "events",
    "linkbio",
    "finance",
    "cells",
    "notifications",
    "custom_forms",
    "checkin",
    "escalas",
    "sermons",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "core.middleware.CurrentUserMiddleware",
    "core.middleware.TenantMiddleware",
]

ROOT_URLCONF = "church_crm.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.church_config",
            ],
        },
    },
]

WSGI_APPLICATION = "church_crm.wsgi.application"


# Database
# SQLite por padrão; troque para PostgreSQL definindo DB_ENGINE=postgresql
# no .env (ver .env.example) — nada mais no código muda.

if os.getenv("DB_ENGINE") == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("DB_NAME", "church_crm"),
            "USER": os.getenv("DB_USER", "church_crm"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "localhost"),
            "PORT": os.getenv("DB_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

AUTH_USER_MODEL = "accounts.User"

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"


# Internationalization

LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Sao_Paulo"
USE_I18N = True
USE_TZ = True


# Static files (CSS, JavaScript, Images)

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # Manifest storage exige `collectstatic` já ter rodado (gera um
    # staticfiles.json mapeando cada arquivo pro seu hash) — em DEBUG isso
    # nunca rodou, então usar a versão com manifest aqui quebraria QUALQUER
    # `{% static %}` no dev server com "Missing staticfiles manifest entry".
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if not DEBUG else "django.contrib.staticfiles.storage.StaticFilesStorage"
        )
    },
}

# Uploads (fotos de membros, capas de eventos, avatar do link-na-bio)
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# E-mail (usado só pela recuperação de senha por enquanto). Em DEBUG cai no
# console — nenhum e-mail sai de verdade sem configurar EMAIL_HOST_USER/
# EMAIL_HOST_PASSWORD reais no .env (ver .env.example).
if DEBUG and not os.getenv("EMAIL_HOST_USER"):
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
else:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True") == "True"
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "naoresponda@igreja.local")


# Hardening de produção. SECURE_SSL_REDIRECT/HSTS ficam desligados por
# padrão mesmo com DEBUG=False (opt-in via .env) porque um proxy reverso
# (Nginx, PythonAnywhere etc.) costuma já forçar HTTPS na borda — ligar os
# dois ao mesmo tempo sem confirmar `SECURE_PROXY_SSL_HEADER` correto pode
# causar loop de redirecionamento. Mesmo raciocínio documentado no
# DEPLOY.md do crm-odonto.
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_SSL_REDIRECT = os.getenv("SECURE_SSL_REDIRECT", "False") == "True"
    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "0"))


# Rastreamento de erros em produção (opcional). Só ativa se SENTRY_DSN
# estiver definido — sem isso, `sentry_sdk` nem precisa estar instalado,
# então não afeta dev/testes (ver requirements.txt, comentado na seção de
# produção junto com gunicorn/psycopg2).
SENTRY_DSN = os.getenv("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0")),
        send_default_pii=False,
    )


# Web Push (notificações push do navegador) — opcional. Gere o par de
# chaves VAPID uma vez (ex.: `python -m pywebpush` ou a lib `py-vapid`) e
# preencha no .env; sem isso, a inscrição/envio de push simplesmente não
# acontece (ver `notifications.PushSubscribeView` / `core.push`).
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "")
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "")
VAPID_CLAIMS_EMAIL = os.getenv("VAPID_CLAIMS_EMAIL", "mailto:naoresponda@igreja.local")


# Evolution API (WhatsApp) — infraestrutura de PLATAFORMA, não por igreja.
# Multi-tenência: um servidor só, operado pelo dono, com uma instância
# isolada por igreja (`core.models.Church.whatsapp_instance`). A igreja
# nunca vê esta URL/chave — só conecta pelo QR code na tela in-app.
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "")


# Mercado Pago da PLATAFORMA (Fase 4 — cobrança automática de assinatura,
# `core/mercadopago_billing.py`) — conta do DONO do sistema, diferente da
# conta de cada igreja (`Church.mercadopago_access_token`, usada só pra
# receber pagamento de evento/doação daquela igreja). Sem isso
# configurado, a tela de assinatura mostra erro ao tentar assinar — o
# resto do sistema funciona normal (cobrança continua manual).
PLATFORM_MERCADOPAGO_ACCESS_TOKEN = os.getenv("PLATFORM_MERCADOPAGO_ACCESS_TOKEN", "")
