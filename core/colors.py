"""Gera uma paleta Tailwind (50-950) a partir de UMA cor de marca, usando só
a stdlib (`colorsys`) — sem depender de nenhuma lib de cor. Mantém hue e
saturação fixos e varia a luminosidade, técnica já usada no crm-odonto
(`tenants/cores.py`) para personalização visual por igreja/clínica."""

import colorsys

# Luminosidade alvo de cada grau da escala Tailwind (light -> dark).
_LIGHTNESS_STEPS = {
    50: 0.97, 100: 0.94, 200: 0.86, 300: 0.74, 400: 0.60,
    500: 0.50, 600: 0.42, 700: 0.34, 800: 0.27, 900: 0.20, 950: 0.13,
}


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _rgb_to_hex(rgb):
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def generate_palette(hex_color):
    try:
        r, g, b = _hex_to_rgb(hex_color)
    except (ValueError, IndexError):
        r, g, b = _hex_to_rgb("#2563eb")  # fallback: o azul padrão do sistema

    hue, _, saturation = colorsys.rgb_to_hls(r, g, b)
    return {
        str(shade): _rgb_to_hex(colorsys.hls_to_rgb(hue, lightness, saturation))
        for shade, lightness in _LIGHTNESS_STEPS.items()
    }
