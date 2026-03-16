"""Web design system generator -- produces color palettes, typography, spacing, and CSS/Tailwind configs."""

from __future__ import annotations

import colorsys
import json


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' to (r, g, b) ints."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _lighten(hex_color: str, factor: float) -> str:
    """Lighten a color by mixing with white."""
    r, g, b = _hex_to_rgb(hex_color)
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return _rgb_to_hex(r, g, b)


def _darken(hex_color: str, factor: float) -> str:
    """Darken a color by mixing with black."""
    r, g, b = _hex_to_rgb(hex_color)
    r = int(r * (1 - factor))
    g = int(g * (1 - factor))
    b = int(b * (1 - factor))
    return _rgb_to_hex(r, g, b)


def _generate_shades(hex_color: str) -> dict[str, str]:
    """Generate a full shade palette (50-950) from a base color."""
    return {
        "50": _lighten(hex_color, 0.95),
        "100": _lighten(hex_color, 0.85),
        "200": _lighten(hex_color, 0.70),
        "300": _lighten(hex_color, 0.50),
        "400": _lighten(hex_color, 0.25),
        "500": hex_color,
        "600": _darken(hex_color, 0.15),
        "700": _darken(hex_color, 0.30),
        "800": _darken(hex_color, 0.45),
        "900": _darken(hex_color, 0.60),
        "950": _darken(hex_color, 0.75),
    }


def _complementary(hex_color: str) -> str:
    """Return the complementary color."""
    r, g, b = _hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h = (h + 0.5) % 1.0
    nr, ng, nb = colorsys.hsv_to_rgb(h, s, v)
    return _rgb_to_hex(int(nr * 255), int(ng * 255), int(nb * 255))


def _analogous(hex_color: str, offset: float = 30 / 360) -> tuple[str, str]:
    """Return two analogous colors."""
    r, g, b = _hex_to_rgb(hex_color)
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    h1 = (h + offset) % 1.0
    h2 = (h - offset) % 1.0
    r1, g1, b1 = colorsys.hsv_to_rgb(h1, s, v)
    r2, g2, b2 = colorsys.hsv_to_rgb(h2, s, v)
    return (
        _rgb_to_hex(int(r1 * 255), int(g1 * 255), int(b1 * 255)),
        _rgb_to_hex(int(r2 * 255), int(g2 * 255), int(b2 * 255)),
    )


# ---------------------------------------------------------------------------
# Style presets
# ---------------------------------------------------------------------------

_STYLE_PRESETS: dict[str, dict] = {
    "modern": {
        "font_heading": "'Inter', sans-serif",
        "font_body": "'Inter', sans-serif",
        "base_size": 16,
        "scale_ratio": 1.25,  # Major Third
        "border_radius": {"sm": "0.25rem", "md": "0.5rem", "lg": "0.75rem", "xl": "1rem", "full": "9999px"},
    },
    "classic": {
        "font_heading": "'Georgia', serif",
        "font_body": "'Georgia', serif",
        "base_size": 18,
        "scale_ratio": 1.333,  # Perfect Fourth
        "border_radius": {"sm": "0.125rem", "md": "0.25rem", "lg": "0.375rem", "xl": "0.5rem", "full": "9999px"},
    },
    "minimal": {
        "font_heading": "'Helvetica Neue', Arial, sans-serif",
        "font_body": "'Helvetica Neue', Arial, sans-serif",
        "base_size": 16,
        "scale_ratio": 1.2,  # Minor Third
        "border_radius": {"sm": "0", "md": "0", "lg": "0.25rem", "xl": "0.5rem", "full": "9999px"},
    },
    "playful": {
        "font_heading": "'Poppins', sans-serif",
        "font_body": "'Nunito', sans-serif",
        "base_size": 16,
        "scale_ratio": 1.333,
        "border_radius": {"sm": "0.5rem", "md": "1rem", "lg": "1.5rem", "xl": "2rem", "full": "9999px"},
    },
}


def _typography_scale(base_size: int, ratio: float, font_heading: str, font_body: str) -> dict:
    """Build a modular typography scale."""
    sizes = {}
    labels = ["xs", "sm", "base", "lg", "xl", "2xl", "3xl", "4xl", "5xl"]
    for i, label in enumerate(labels):
        exp = i - 2  # base (index 2) is exponent 0
        px = round(base_size * (ratio ** exp), 2)
        sizes[label] = f"{px}px"
    return {
        "font_heading": font_heading,
        "font_body": font_body,
        "base_size": f"{base_size}px",
        "scale_ratio": ratio,
        "sizes": sizes,
        "line_heights": {
            "tight": "1.25",
            "normal": "1.5",
            "relaxed": "1.75",
        },
        "font_weights": {
            "normal": "400",
            "medium": "500",
            "semibold": "600",
            "bold": "700",
        },
    }


def _spacing_system() -> dict:
    """Generate a spacing / sizing scale (in rem)."""
    steps = {
        "0": "0",
        "0.5": "0.125rem",
        "1": "0.25rem",
        "1.5": "0.375rem",
        "2": "0.5rem",
        "2.5": "0.625rem",
        "3": "0.75rem",
        "4": "1rem",
        "5": "1.25rem",
        "6": "1.5rem",
        "8": "2rem",
        "10": "2.5rem",
        "12": "3rem",
        "16": "4rem",
        "20": "5rem",
        "24": "6rem",
        "32": "8rem",
        "40": "10rem",
        "48": "12rem",
        "56": "14rem",
        "64": "16rem",
    }
    return steps


def _build_css_variables(colors: dict, typography: dict, spacing: dict, border_radius: dict) -> str:
    """Render a :root CSS custom-properties block."""
    lines = [":root {"]

    # Colors
    for palette_name, shades in colors.items():
        if isinstance(shades, dict):
            for shade, value in shades.items():
                lines.append(f"  --color-{palette_name}-{shade}: {value};")
        else:
            lines.append(f"  --color-{palette_name}: {shades};")

    # Typography
    lines.append(f"  --font-heading: {typography['font_heading']};")
    lines.append(f"  --font-body: {typography['font_body']};")
    for name, size in typography["sizes"].items():
        lines.append(f"  --text-{name}: {size};")

    # Spacing
    for name, value in spacing.items():
        lines.append(f"  --spacing-{name}: {value};")

    # Border radius
    for name, value in border_radius.items():
        lines.append(f"  --radius-{name}: {value};")

    lines.append("}")
    return "\n".join(lines)


def _build_tailwind_config(colors: dict, typography: dict) -> str:
    """Generate a partial Tailwind CSS config (theme.extend)."""
    tw_colors = {}
    for palette_name, shades in colors.items():
        if isinstance(shades, dict):
            tw_colors[palette_name] = shades
        else:
            tw_colors[palette_name] = shades

    config = {
        "theme": {
            "extend": {
                "colors": tw_colors,
                "fontFamily": {
                    "heading": [typography["font_heading"]],
                    "body": [typography["font_body"]],
                },
                "fontSize": typography["sizes"],
            }
        }
    }
    return json.dumps(config, indent=2)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    brand_name: str = "",
    primary_color: str = "#3b82f6",
    style: str = "modern",
) -> dict:
    """Generate a complete design system.

    Args:
        brand_name: Optional brand name for documentation.
        primary_color: Primary brand hex color.
        style: Design style preset -- 'modern', 'classic', 'minimal', or 'playful'.

    Returns:
        dict with keys: colors, typography, spacing, css_variables, tailwind_config.
    """
    style = style.lower().strip()
    if style not in _STYLE_PRESETS:
        style = "modern"

    preset = _STYLE_PRESETS[style]

    # Build color palettes
    secondary_color = _complementary(primary_color)
    accent1, accent2 = _analogous(primary_color)

    colors: dict = {
        "primary": _generate_shades(primary_color),
        "secondary": _generate_shades(secondary_color),
        "accent": _generate_shades(accent1),
        "neutral": _generate_shades("#6b7280"),
        "success": _generate_shades("#10b981"),
        "warning": _generate_shades("#f59e0b"),
        "error": _generate_shades("#ef4444"),
    }

    # Typography
    typography = _typography_scale(
        base_size=preset["base_size"],
        ratio=preset["scale_ratio"],
        font_heading=preset["font_heading"],
        font_body=preset["font_body"],
    )

    # Spacing
    spacing = _spacing_system()

    # CSS variables
    css_variables = _build_css_variables(colors, typography, spacing, preset["border_radius"])

    # Tailwind config
    tailwind_config = _build_tailwind_config(colors, typography)

    return {
        "colors": colors,
        "typography": typography,
        "spacing": spacing,
        "border_radius": preset["border_radius"],
        "css_variables": css_variables,
        "tailwind_config": tailwind_config,
    }
