"""Session-scoped PENELCO presentation tokens; no data or analytical state lives here.

Read tokens inside figure-building functions, never once at module import time.
The shared URL preference restores the mode after a browser refresh.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import plotly.graph_objects as go
import streamlit as st

THEME_KEY = "ui_theme_mode"
THEME_MODES = ("Normal", "Colorblind")

# Light geographic context beneath analytical overlays in both display modes.
# Plotly's built-in CARTO/OSM tiles retain attribution and need no API key.
# Tiles load in the browser; source and processed GIS datasets stay unchanged.
MAP_BASE_STYLE = "carto-positron"

NORMAL_BRAND = {
    "primary_blue": "#1428C8", "electric_blue": "#165DFF", "orange": "#FF7800",
    "deep_orange": "#D95600", "teal": "#00A8A8", "purple": "#7B61FF",
    "navy": "#071A72", "charcoal": "#1F2328", "slate": "#667085",
    "gray": "#E5E7EB", "background": "#F7F9FC", "surface": "#FFFFFF",
}
COLORBLIND_BRAND = {
    "deep_blue": "#0D47A1", "blue": "#1976D2", "purple": "#5E35B1",
    "magenta": "#C2185B", "violet": "#8E24AA", "navy": "#0F172A",
    "charcoal": "#334155", "slate": "#64748B", "gray": "#CBD5E1",
    "background": "#F1F5F9", "surface": "#FFFFFF",
}

# Dark text variants are intentional: brand orange/teal are not body-text colors.
_NORMAL = {
    "primary": NORMAL_BRAND["primary_blue"], "secondary": NORMAL_BRAND["electric_blue"],
    "accent": NORMAL_BRAND["orange"], "accent_text": "#A33F00", "selected": NORMAL_BRAND["deep_orange"],
    "selected_background": "#FFF0E3", "warning": "#A33F00", "warning_background": "#FFF0E3",
    "critical": "#9D3100", "critical_background": "#FCEAE4", "success": "#00767A",
    "success_background": "#E7F6F5", "info_background": "#EAF0FF",
    "heading": NORMAL_BRAND["navy"], "text": NORMAL_BRAND["charcoal"], "muted": NORMAL_BRAND["slate"],
    "border": NORMAL_BRAND["gray"], "control_border": "#858C9A", "grid": "#E5E7EB",
    "background": NORMAL_BRAND["background"], "surface": NORMAL_BRAND["surface"],
    "observed": NORMAL_BRAND["primary_blue"], "modeled": "#796891", "transformer": "#7650A5",
    "pole": "#007E91", "affected": NORMAL_BRAND["deep_orange"], "source": "#9D3100",
    "inactive": "#737C8C", "relationship": "#4C5365", "boundary": "#96A1B3",
    "municipality_boundary": "#374151", "affected_boundary": "#667085", "missing": "#D7DDE2",
    "weather": NORMAL_BRAND["deep_orange"], "equipment": NORMAL_BRAND["primary_blue"],
    "vegetation": "#007F83", "other": "#7650A5",
    "reference_municipality": "#A33F00", "reference_province": NORMAL_BRAND["slate"],
}
_COLORBLIND = {
    "primary": COLORBLIND_BRAND["deep_blue"], "secondary": COLORBLIND_BRAND["blue"],
    "accent": COLORBLIND_BRAND["purple"], "accent_text": COLORBLIND_BRAND["purple"],
    "selected": COLORBLIND_BRAND["magenta"], "selected_background": "#F9E9F0",
    "warning": COLORBLIND_BRAND["violet"], "warning_background": "#F4EAF8",
    "critical": "#880E4F", "critical_background": "#F9E9F0", "success": COLORBLIND_BRAND["deep_blue"],
    "success_background": "#E9F1FA", "info_background": "#E9F1FA",
    "heading": COLORBLIND_BRAND["navy"], "text": COLORBLIND_BRAND["charcoal"],
    "muted": "#59687E", "border": COLORBLIND_BRAND["gray"], "control_border": "#7B899E", "grid": "#E2E8F0",
    "background": COLORBLIND_BRAND["background"], "surface": COLORBLIND_BRAND["surface"],
    "observed": COLORBLIND_BRAND["deep_blue"], "modeled": "#8B80A1", "transformer": COLORBLIND_BRAND["violet"],
    "pole": COLORBLIND_BRAND["blue"], "affected": COLORBLIND_BRAND["purple"], "source": COLORBLIND_BRAND["magenta"],
    "inactive": COLORBLIND_BRAND["slate"], "relationship": "#475569", "boundary": "#94A3B8",
    "municipality_boundary": COLORBLIND_BRAND["charcoal"], "affected_boundary": COLORBLIND_BRAND["slate"],
    "missing": "#D5DDE7", "weather": COLORBLIND_BRAND["magenta"], "equipment": COLORBLIND_BRAND["deep_blue"],
    "vegetation": COLORBLIND_BRAND["purple"], "other": COLORBLIND_BRAND["slate"],
    "reference_municipality": COLORBLIND_BRAND["purple"], "reference_province": "#59687E",
}


@dataclass(frozen=True)
class Theme:
    name: str
    colors: Mapping[str, str]
    brand: Mapping[str, str]


THEMES = MappingProxyType({
    "Normal": Theme("Normal", MappingProxyType(_NORMAL), MappingProxyType(NORMAL_BRAND)),
    "Colorblind": Theme("Colorblind", MappingProxyType(_COLORBLIND), MappingProxyType(COLORBLIND_BRAND)),
})


def get_theme(mode: str | None = None) -> Theme:
    if mode is None:
        mode = st.session_state.get(THEME_KEY, "Normal")
        if mode not in THEMES:
            mode = "Normal"
    return THEMES[mode]


def get_color(role: str, *, mode: str | None = None) -> str:
    return get_theme(mode).colors[role]


class SemanticColors(Mapping):
    """Compatibility for existing color dictionaries, resolved per session/read."""
    def __init__(self, roles):
        self.roles = MappingProxyType(dict(roles))

    def __getitem__(self, key):
        return get_color(self.roles[key])

    def __iter__(self):
        return iter(self.roles)

    def __len__(self):
        return len(self.roles)


def get_categorical_palette(*, mode=None):
    return [get_color(role, mode=mode) for role in ("equipment", "weather", "vegetation", "other", "secondary", "critical")]


# Environmental scales are identical in both modes. Ordered lightness preserves
# numeric interpretation and lets cached raster previews be reused unchanged.
_CONTINUOUS = {
    "ndvi": ((0, "#F5F2FA"), (.5, "#D5DAEA"), (.7, "#83B8CB"), (.85, "#367F9C"), (1, "#163955")),
    "elevation": ((0, "#EEF5FB"), (.35, "#70B6C7"), (.7, "#7860A0"), (1, "#30204F")),
    "Normal": ((0, "#F0F4FF"), (.25, "#C1D1FA"), (.5, "#7F9CE6"), (.75, "#375EB8"), (1, "#071A72")),
    "Colorblind": ((0, "#EEF4FC"), (.25, "#C2D6EC"), (.5, "#79A5D2"), (.75, "#376FAB"), (1, "#0D315E")),
}


def get_sequential_scale(kind="magnitude", *, mode=None):
    key = kind if kind in {"ndvi", "elevation"} else get_theme(mode).name
    return [list(stop) for stop in _CONTINUOUS[key]]


_CAUSE_ROLES = {
    "Equipment / Technical": ("equipment", "", "circle"),
    "Weather / Storm": ("weather", "/", "square"),
    "Vegetation / Tree Contact": ("vegetation", "x", "diamond"),
    "Other / Unknown": ("other", ".", "triangle-up"),
}


def get_cause_style(cause, *, mode=None):
    role, pattern, symbol = _CAUSE_ROLES.get(cause, ("other", ".", "triangle-up"))
    return {"color": get_color(role, mode=mode), "pattern": pattern, "symbol": symbol}


def get_reference_style(label, *, mode=None):
    role, dash, symbol = ({
        "Selected barangay": ("primary", "solid", "circle"),
        "Municipality avg barangay": ("reference_municipality", "dash", "square"),
        "Municipality avg": ("reference_municipality", "dash", "square"),
        "Bataan avg barangay": ("reference_province", "dot", "diamond"),
        "Bataan avg": ("reference_province", "dot", "diamond"),
    })[label]
    return {"color": get_color(role, mode=mode), "dash": dash, "symbol": symbol}


def get_map_styles(*, mode=None):
    """Shared provenance/state grammar. Dashes are MapLibre paint units."""
    c = lambda role: get_color(role, mode=mode)
    return {
        "observed": {"color": c("observed"), "width": 3.4, "dash": "solid", "line_dash": [1, 0], "symbol": "square"},
        "modeled": {"color": c("modeled"), "width": 1.4, "dash": "dash", "line_dash": [5, 3], "symbol": "circle"},
        "pole": {"color": c("pole"), "size": 6, "symbol": "circle"},
        "transformer": {"color": c("transformer"), "size": 9, "symbol": "diamond"},
        "affected": {"color": c("affected"), "width": 3.3, "dash": "solid", "line_dash": [1, 0], "symbol": "diamond"},
        "source": {"color": c("source"), "size": 20, "symbol": "x"},
        "relationship": {"color": c("relationship"), "width": 1.7, "dash": "dot", "line_dash": [3, 3]},
        "selected": {"color": c("selected"), "width": 3, "casing": c("surface"), "casing_width": 5},
        "boundary": {"color": c("boundary"), "width": .75, "opacity": .65},
        "labels": {"color": c("text"), "background": rgba(c("surface"), .9)},
    }


def rgba(color, opacity):
    rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
    return f"rgba({rgb[0]},{rgb[1]},{rgb[2]},{opacity})"


def style_plotly_figure(fig, *, mode=None):
    """Style presentation only; preserve data, camera, UI revisions and sizing."""
    c = lambda role: get_color(role, mode=mode)
    fig.update_layout(
        paper_bgcolor=c("surface"), plot_bgcolor=c("surface"),
        font=dict(family="sans-serif", color=c("text"), size=12),
        colorway=get_categorical_palette(mode=mode),
        hoverlabel=dict(bgcolor=c("surface"), bordercolor=c("control_border"), font=dict(color=c("text"), size=12)),
        legend=dict(font=dict(color=c("text")), bgcolor=c("surface")),
    )
    fig.update_xaxes(gridcolor=c("grid"), zerolinecolor=c("grid"), color=c("muted"))
    fig.update_yaxes(gridcolor=c("grid"), zerolinecolor=c("grid"), color=c("muted"))
    for trace in fig.data:
        if trace.type != "bar":
            continue
        color = trace.marker.color
        def text_on(fill):
            return c("surface") if contrast_ratio(c("surface"), fill) >= 4.5 else c("text")
        if isinstance(color, str) and color.startswith("#"):
            trace.insidetextfont.color = text_on(color)
        elif isinstance(color, (list, tuple)) and all(isinstance(v, str) and v.startswith("#") for v in color):
            trace.insidetextfont.color = [text_on(v) for v in color]
        trace.outsidetextfont.color = c("text")
    return fig


def add_map_legend(fig, name, color, *, symbol=None, width=2, dash="solid", group=None):
    """Cartesian legend symbols accurately represent custom MapLibre sprites."""
    fig.add_trace(go.Scatter(x=[None], y=[None], mode="markers" if symbol else "lines", name=name,
                            legendgroup=group or name, hoverinfo="skip",
                            marker=dict(symbol=symbol or "circle", color=color, size=10,
                                        line=dict(color=get_color("surface"), width=1)),
                            line=dict(color=color, width=width, dash=dash)))
    fig.update_xaxes(visible=False, fixedrange=True)
    fig.update_yaxes(visible=False, fixedrange=True)


def contrast_ratio(foreground, background):
    def luminance(color):
        rgb = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    bright, dark = sorted([luminance(foreground), luminance(background)], reverse=True)
    return (bright + .05) / (dark + .05)


def app_css(mode=None):
    colors = get_theme(mode).colors
    variables = ";".join(f"--pen-{key.replace('_', '-')}: {value}" for key, value in colors.items())
    css = (Path(__file__).parent / "theme.css").read_text(encoding="utf-8")
    return f"<style>:root {{{variables};}}\n{css}</style>"


def apply_app_theme():
    """Call once before page.run; use public state/URL APIs for refresh support.

    Streamlit 1.64's bind="query-params" conflicts with st.switch_page clearing
    bound parameters. Keep this unbound and synchronize the preference here.
    Existing session state wins on navigation; a new session reads the URL.
    """
    if THEME_KEY not in st.session_state:
        requested = st.query_params.get(THEME_KEY, "Normal")
        st.session_state[THEME_KEY] = requested if requested in THEME_MODES else "Normal"
    with st.container(key="theme_toolbar", horizontal=True, horizontal_alignment="right"):
        with st.popover("Display mode", icon=":material/contrast:", key="theme_menu"):
            st.segmented_control("Display mode", THEME_MODES, required=True,
                                 key=THEME_KEY, width="content",
                                 help="Normal uses PENELCO blue/orange. Colorblind uses blue/purple/magenta. "
                                      "Shapes, patterns and labels distinguish categories in both modes. "
                                      "The current URL retains this preference after refresh.")
    if st.query_params.get(THEME_KEY) != st.session_state[THEME_KEY]:
        st.query_params[THEME_KEY] = st.session_state[THEME_KEY]
    st.html(app_css())

