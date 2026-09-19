"""MediGuide brand mark and icon set.

The mark is a guided-pulse glyph: a vital-signs trace that rises and turns into
a navigation arrowhead. It carries the two ideas the product is actually about -
health, and being guided to the right next step - without falling back on a
medical cross, a hospital block or a brain.

It is drawn on a 32x32 grid with heavy strokes and no fine detail, so it stays
legible as a 16px favicon.

Icons are inline SVG from one consistent 24x24 line set (2px stroke, round caps).
No emoji and no Unicode dingbats are used as interface icons: those render
differently on every platform and were the source of the corrupted glyphs this
file replaces.
"""

from __future__ import annotations

from urllib.parse import quote

BRAND_NAME = "MediGuide"

# --- brand mark ------------------------------------------------------------

def _mark_paths(stroke: str) -> str:
    """The glyph itself: pulse trace rising into an arrowhead."""
    return (
        f"<path d='M4 19h5.2l2.4-6.4 3.1 9.2 2.3-5.1h2.1' fill='none' stroke='{stroke}' "
        "stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round'/>"
        f"<path d='M20.4 16.9 27 10.3' fill='none' stroke='{stroke}' stroke-width='2.6' "
        "stroke-linecap='round'/>"
        f"<path d='M21.9 9.4h5.4v5.4' fill='none' stroke='{stroke}' stroke-width='2.6' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
    )


def logo_svg(size: int = 36, *, tile: bool = True) -> str:
    """The lockup mark. `tile` draws the rounded badge behind the glyph."""
    background = (
        "<defs><linearGradient id='mgGrad' x1='0' y1='0' x2='1' y2='1'>"
        "<stop offset='0' stop-color='#13977b'/><stop offset='1' stop-color='#0f7a63'/>"
        "</linearGradient></defs>"
        "<rect x='0' y='0' width='32' height='32' rx='9' fill='url(#mgGrad)'/>"
    ) if tile else ""
    stroke = "#ffffff" if tile else "currentColor"
    return (
        f"<svg viewBox='0 0 32 32' width='{size}' height='{size}' role='img' "
        f"aria-label='{BRAND_NAME}' focusable='false' class='brand-mark'>"
        f"{background}{_mark_paths(stroke)}</svg>"
    )


def favicon_data_uri() -> str:
    """Standalone SVG favicon. Same glyph, so the tab matches the header."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        "<rect width='32' height='32' rx='9' fill='#0f7a63'/>"
        + _mark_paths("#ffffff")
        + "</svg>"
    )
    return "data:image/svg+xml," + quote(svg, safe="")


def logo_lockup(href: str = "/ui") -> str:
    """Mark plus wordmark, used in the header."""
    return (
        f"<a class='logo' href='{href}' aria-label='{BRAND_NAME} home'>"
        f"{logo_svg(34)}<span class='logo__word'>Medi<span class='logo__word-accent'>Guide</span></span></a>"
    )


# --- icon set --------------------------------------------------------------
# One 24x24 line set. Paths only; the wrapper adds sizing and accessibility.

_ICON_PATHS = {
    "chat": "M21 11.5a8.4 8.4 0 0 1-9 8.4L7 22v-3.3A8.4 8.4 0 1 1 21 11.5Z",
    "stethoscope": "M6 3v5a4 4 0 0 0 8 0V3M10 12v2a5 5 0 0 0 10 0v-1M20 9a1.5 1.5 0 1 1 0 3 1.5 1.5 0 0 1 0-3Z",
    "calendar": "M4 6.5A1.5 1.5 0 0 1 5.5 5h13A1.5 1.5 0 0 1 20 6.5v12A1.5 1.5 0 0 1 18.5 20h-13A1.5 1.5 0 0 1 4 18.5ZM8 3v4M16 3v4M4 10h16",
    "clock": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7.5V12l3 2",
    "bell": "M18 9a6 6 0 1 0-12 0c0 5-2 6-2 6h16s-2-1-2-6ZM10.3 20a2 2 0 0 0 3.4 0",
    "user": "M20 21v-1.5a5 5 0 0 0-5-5H9a5 5 0 0 0-5 5V21M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
    "search": "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM20 20l-4-4",
    "shield": "M12 3 5 6v5.5c0 4.3 2.9 8.2 7 9.5 4.1-1.3 7-5.2 7-9.5V6Z",
    "check": "M5 12.5 10 17.5 19 7",
    "alert": "M12 8.5V13M12 16.5v.5M10.3 4.3 2.8 17.5A1.4 1.4 0 0 0 4 19.6h16a1.4 1.4 0 0 0 1.2-2.1L13.7 4.3a1.4 1.4 0 0 0-2.4 0Z",
    "info": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 11v5M12 8v.5",
    "pin": "M12 21s7-5.6 7-11a7 7 0 1 0-14 0c0 5.4 7 11 7 11ZM12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z",
    "arrow-right": "M5 12h13M13 6.5 18.5 12 13 17.5",
    "arrow-left": "M19 12H6M11 17.5 5.5 12 11 6.5",
    "external": "M14 5h5v5M19 5l-7.5 7.5M18 14.5V18a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 18V8a1.5 1.5 0 0 1 1.5-1.5H10",
    "document": "M14 3H7.5A1.5 1.5 0 0 0 6 4.5v15A1.5 1.5 0 0 0 7.5 21h9a1.5 1.5 0 0 0 1.5-1.5V7ZM14 3v4h4M9 13h6M9 16.5h4",
    "logout": "M15 17.5 19.5 13 15 8.5M19 13H9M11 4.5H6.5A1.5 1.5 0 0 0 5 6v14a1.5 1.5 0 0 0 1.5 1.5H11",
    "moon": "M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z",
    "sun": "M12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9ZM12 2.5V4M12 20v1.5M4 12H2.5M21.5 12H20M5.6 5.6 4.6 4.6M19.4 19.4l-1-1M18.4 5.6l1-1M5.6 18.4l-1 1",
}


def icon(name: str, *, size: int = 20, label: str = "") -> str:
    """Inline SVG icon.

    Decorative by default (aria-hidden); pass `label` when the icon is the only
    thing conveying meaning.
    """
    path = _ICON_PATHS.get(name)
    if path is None:
        return ""
    accessibility = (
        f"role='img' aria-label='{label}'" if label else "aria-hidden='true'"
    )
    return (
        f"<svg viewBox='0 0 24 24' width='{size}' height='{size}' fill='none' "
        f"stroke='currentColor' stroke-width='1.9' stroke-linecap='round' "
        f"stroke-linejoin='round' class='icon icon--{name}' focusable='false' {accessibility}>"
        f"<path d='{path}'/></svg>"
    )


def head_links() -> str:
    """Favicon and theme colour for the document head."""
    return (
        f"<link rel='icon' type='image/svg+xml' href='{favicon_data_uri()}'>"
        f"<link rel='apple-touch-icon' href='{favicon_data_uri()}'>"
        "<meta name='theme-color' content='#0f7a63'>"
    )


ICON_NAMES = tuple(sorted(_ICON_PATHS))
