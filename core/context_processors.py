from django.conf import settings

from core.colors import generate_palette


def church_config(request):
    """Disponibiliza os dados da igreja (nome, pastor, logo, paleta de
    cores) em todos os templates, sem precisar passar isso view a view.
    `request.church` vem de `TenantMiddleware` — `None` pra quem não tem
    igreja (dono da plataforma) ou pra uma página pública ainda não
    resolvida (cai numa paleta/nome padrão em vez de quebrar)."""
    config = getattr(request, "church", None)
    palette_source = config.brand_color if config else "#2563eb"
    return {
        "church_config": config,
        "paleta_marca": generate_palette(palette_source),
        "vapid_public_key": settings.VAPID_PUBLIC_KEY,
    }
