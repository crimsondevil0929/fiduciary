"""The marks of the three packages: AgentGov, Interlock and Fiduciary (1.15.0).

One drawing of each, on a 32-unit grid, in three inks:

- ``i``: the structure, solid (the ring and beam of a meter, a signed facet);
- ``f``: what is not final yet, a faint fill, often under a dashed line ``o`` (held budget, a
  staged copy, a principal who has not signed);
- ``a``: what is final, in the package's own color (spent and recorded, committed, sealed).

AgentGov is a circle (a meter whose lower half is spent, read on a level beam), Interlock a
square (a staged half and a committed half, dovetailed along a seam), Fiduciary a triangle
(three facets, two signed, around the seal they form).

:func:`standalone` writes a mark as an SVG file (each repository's ``docs/logo.svg`` and
``docs/logo-dark.svg``); :func:`inline` writes it for a page whose stylesheet colors the
classes (the console). ``scripts/make_logos.py`` writes the files.
"""

from __future__ import annotations

from typing import Final

__all__ = ["ACCENTS", "MARKS", "NAMES", "inline", "standalone"]

NAMES: Final = ("agentgov", "interlock", "fiduciary")
TITLES: Final = {"agentgov": "AgentGov", "interlock": "Interlock", "fiduciary": "Fiduciary"}

INK: Final = ("#121212", "#ecebe6")
"""The structure's ink: on a light page, on a dark one."""

ACCENTS: Final = {
    "agentgov": ("#b8740b", "#f2b84b"),
    "interlock": ("#2e66c8", "#86acf2"),
    "fiduciary": ("#087a57", "#4fd6a5"),
}
"""Each package's color, on a light page and on a dark one."""

_SEAM: Final = "M16 0V8.6L12.4 7.4V13.6L16 12.4V19.6L19.6 18.4V24.6L16 23.4V32"

MARKS: Final = {
    "agentgov": (
        '<circle class="o" cx="16" cy="16" r="12" stroke-width="2"/>'
        '<path class="f" d="M8.25 14A7.75 7.75 0 0 1 23.75 14Z"/>'
        '<path class="o" d="M8.51 14A7.75 7.75 0 0 1 23.49 14" stroke-width="1.6" '
        'stroke-dasharray="2.2 1.9"/>'
        '<path class="a" d="M7.68 17.75A8.5 8.5 0 0 0 24.32 17.75Z"/>'
        '<path class="o" d="M1 16H31" stroke-width="2"/>'
    ),
    "interlock": (
        '<clipPath id="{id}l"><path d="M0 0H16V8.6L12.4 7.4V13.6L16 12.4V19.6L19.6 18.4V24.6'
        'L16 23.4V32H0Z"/></clipPath>'
        '<clipPath id="{id}r"><path d="M32 0H16V8.6L12.4 7.4V13.6L16 12.4V19.6L19.6 18.4V24.6'
        'L16 23.4V32H32Z"/></clipPath>'
        '<mask id="{id}m" maskUnits="userSpaceOnUse" x="0" y="0" width="32" height="32">'
        '<rect width="32" height="32" fill="#fff"/>'
        f'<path d="{_SEAM}" stroke="#000" stroke-width="1.7" fill="none"/></mask>'
        '<g mask="url(#{id}m)"><g clip-path="url(#{id}l)">'
        '<rect class="f" x="3" y="3" width="26" height="26" rx="3.2"/>'
        '<rect class="o" x="3.8" y="3.8" width="24.4" height="24.4" rx="2.4" stroke-width="1.6" '
        'stroke-dasharray="2.3 1.7" stroke-dashoffset="1"/></g>'
        '<rect class="a" x="3" y="3" width="26" height="26" rx="3.2" '
        'clip-path="url(#{id}r)"/></g>'
    ),
    "fiduciary": (
        '<mask id="{id}m" maskUnits="userSpaceOnUse" x="0" y="0" width="32" height="32">'
        '<rect width="32" height="32" fill="#fff"/>'
        '<path d="M16 0L16 19.33M-0.75 29L16 19.33M32.75 29L16 19.33" stroke="#000" '
        'stroke-width="1.7"/></mask>'
        '<g mask="url(#{id}m)">'
        '<path class="i" d="M16 3L1.85 27.5L7.34 24.33L16 9.33Z"/>'
        '<path class="i" d="M1.85 27.5L30.15 27.5L24.66 24.33L7.34 24.33Z"/>'
        '<path class="f" d="M30.15 27.5L16 3L16 9.33L24.66 24.33Z"/></g>'
        '<path class="a" d="M16 12.73L10.28 22.63L21.72 22.63Z"/>'
    ),
}
"""Each mark's drawing, its ids written ``{id}`` so two on one page do not collide."""


def _body(name: str, uid: str) -> str:
    return MARKS[name].replace("{id}", uid)


def standalone(name: str, *, theme: str = "light", size: int = 64) -> str:
    """The mark as an SVG file of its own: drawn for a light page, a dark one, or (``auto``)
    whichever the reader's system is set to.

    A file shown in an ``<img>`` follows the operating system's setting, not the page's, so a
    README picks the light or the dark file with ``<picture>``, which GitHub matches to its
    own theme.
    """
    (ink, ink_dark), (accent, accent_dark) = INK, ACCENTS[name]
    if theme not in ("light", "dark", "auto"):
        raise ValueError(f"theme is light, dark or auto, not {theme!r}")
    title = TITLES[name]
    if theme == "dark":
        ink, accent = ink_dark, accent_dark
    faint = ".24" if theme == "dark" else ".2"
    style = (
        f".i{{fill:{ink}}}.o{{fill:none;stroke:{ink}}}.f{{fill:{ink};fill-opacity:{faint}}}"
        f".a{{fill:{accent}}}"
    )
    if theme == "auto":
        style += (
            "\n@media (prefers-color-scheme:dark){"
            f".i{{fill:{ink_dark}}}.o{{stroke:{ink_dark}}}.f{{fill:{ink_dark};fill-opacity:.24}}"
            f".a{{fill:{accent_dark}}}}}"
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 32 32" role="img" aria-label="{title}">\n'
        f"<title>{title}</title>\n"
        f"<style>\n{style}\n</style>\n"
        f"{_body(name, name[0])}\n"
        "</svg>\n"
    )


def inline(name: str, uid: str) -> str:
    """The mark for a page whose stylesheet colors ``.mk .i``, ``.o``, ``.f`` and
    ``.mk-<name> .a``."""
    return (
        f'<svg class="mk mk-{name}" viewBox="0 0 32 32" aria-hidden="true" focusable="false">'
        f"{_body(name, uid)}</svg>"
    )
