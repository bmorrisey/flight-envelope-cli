"""Generic Plotly renderer: any :class:`EnvelopeData` -> self-contained HTML.

Doubles as a reference implementation for client-side re-plotting of the
exported JSON contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..models.outputs import EnvelopeData
from ..palette import PALETTE, resolve_color

_DASH = {"solid": "solid", "dashed": "dash", "dotted": "dot", "dashdot": "dashdot"}


def _plain(text: str) -> str:
    """Matplotlib mathtext -> Plotly-friendly HTML-ish text."""
    t = text.replace("$", "")
    t = re.sub(r"\\pm", "±", t)
    t = re.sub(r"\\times", "×", t)
    t = re.sub(r"\\omega", "ω", t)
    t = re.sub(r"_\{([^}]*)\}", r"<sub>\1</sub>", t)
    t = re.sub(r"_(\w)", r"<sub>\1</sub>", t)
    t = re.sub(r"\^\{?(\*)\}?", r"\1", t)
    return t.replace("\\", "")


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


def build_figure(env: EnvelopeData, theme: str = "light"):
    try:
        import plotly.graph_objects as go
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("interactive output needs plotly: pip install 'flight-envelope[interactive]'") from exc

    p = PALETTE[theme]
    fig = go.Figure()
    for reg in env.regions:
        fig.add_trace(go.Scatter(
            x=[pt.x for pt in reg.points], y=[pt.y for pt in reg.points], fill="toself", mode="lines",
            line=dict(width=0), fillcolor=_rgba(resolve_color(reg.color_hint, theme), reg.opacity),
            name=_plain(reg.label), hoverinfo="name", legendgroup="regions", showlegend=True))
    for kind, items in (("contour", env.contours or []), ("boundary", env.boundaries)):
        for s in items:
            fig.add_trace(go.Scatter(
                x=[pt.x for pt in s.points], y=[pt.y for pt in s.points], mode="lines",
                line=dict(color=resolve_color(s.color_hint, theme), width=s.line_width * 1.2,
                          dash=_DASH[s.line_style]),
                name=_plain(s.inline_label or s.label), legendgroup=s.role or kind,
                showlegend=s.show_in_legend or kind == "boundary",
                hovertemplate=f"{_plain(s.label)}<br>%{{x:.1f}} {s.x_unit}, %{{y:.2f}} {s.y_unit}<extra></extra>"))
    if env.key_points:
        kps = list(env.key_points.values())
        fig.add_trace(go.Scatter(
            x=[k.x for k in kps], y=[k.y for k in kps], mode="markers+text",
            text=[_plain(k.label) for k in kps], textposition="top center",
            marker=dict(size=9, color=p["cobalt"], line=dict(color=p["background"], width=1.5)),
            customdata=[k.description for k in kps], name="Key points",
            hovertemplate="%{text}: %{customdata}<br>%{x:.1f}, %{y:.2f}<extra></extra>"))

    fc = env.flight_conditions
    fig.update_layout(
        template="plotly_dark" if theme == "dark" else "plotly_white",
        title=dict(text=f"{env.title}<br><sup>m = {fc['mass_kg']:,.0f} kg · h = {fc['altitude_ft']:,.0f} ft · "
                        f"{fc['regulation'].upper()}</sup>"),
        xaxis=dict(title=env.x_axis.label, range=[env.x_axis.min, env.x_axis.max]),
        yaxis=dict(title=_plain(env.y_axis.label), range=[env.y_axis.min, env.y_axis.max]),
        paper_bgcolor=p["background"], plot_bgcolor=p["background"],
        legend=dict(groupclick="toggleitem"), hovermode="closest",
    )
    return fig


def write_html(env: EnvelopeData, path: str | Path, theme: str = "light") -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    build_figure(env, theme).write_html(str(path), include_plotlyjs=True, full_html=True)
    return path
