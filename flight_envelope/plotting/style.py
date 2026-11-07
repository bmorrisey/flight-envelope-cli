"""Shared Matplotlib styling and drawing primitives.

Renderers use the object-oriented API (``matplotlib.figure.Figure``) only, so
they never touch pyplot global state or require a display backend.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable

import numpy as np
from matplotlib.figure import Figure

from ..models.outputs import CurveSeries, EnvelopeData, Region
from ..palette import PALETTE, resolve_color

LINESTYLES = {"solid": "-", "dashed": (0, (5, 3)), "dotted": (0, (1, 2)), "dashdot": (0, (6, 2, 1.5, 2))}
THEMES = tuple(PALETTE)
SUPPORTED_FORMATS = {".png", ".svg", ".pdf"}


def rc_params(theme: str = "light") -> dict:
    p = PALETTE[theme]
    return {
        "figure.facecolor": p["background"],
        "axes.facecolor": p["background"],
        "savefig.facecolor": p["background"],
        "axes.edgecolor": p["muted"],
        "axes.labelcolor": p["text"],
        "axes.titlecolor": p["text"],
        "axes.linewidth": 0.8,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": p["grid"],
        "grid.alpha": 0.2,
        "grid.linewidth": 0.7,
        "xtick.color": p["muted"],
        "ytick.color": p["muted"],
        "xtick.labelcolor": p["text"],
        "ytick.labelcolor": p["text"],
        "xtick.direction": "out",
        "ytick.direction": "out",
        "xtick.major.size": 3,
        "ytick.major.size": 3,
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.titlesize": 14,
        "axes.titleweight": "normal",
        "axes.labelsize": 11,
        "legend.fontsize": 8.5,
        "legend.frameon": True,
        "legend.framealpha": 0.92,
        "legend.facecolor": p["background"],
        "legend.edgecolor": p["grid"],
        "legend.labelcolor": p["text"],
        "mathtext.fontset": "dejavusans",
        "text.color": p["text"],
    }


def new_figure(theme: str, size=(11.5, 6.8)) -> Figure:
    import matplotlib as mpl

    with mpl.rc_context(rc_params(theme)):
        fig = Figure(figsize=size, layout="constrained")
        fig.add_subplot(111)
    return fig


def draw_region(ax, region: Region, theme: str, zorder: float = 0.5) -> None:
    xy = np.array([[p.x, p.y] for p in region.points])
    if len(xy) < 3:
        return
    c = resolve_color(region.color_hint, theme)
    ax.fill(xy[:, 0], xy[:, 1], facecolor=c, alpha=region.opacity, edgecolor="none",
            zorder=zorder, label=region.label if region.show_in_legend else "_nolegend_",
            hatch=region.hatch)


def draw_series(ax, s: CurveSeries, theme: str, zorder: float = 2.0, legend: bool = True):
    xy = np.array([[p.x, p.y] for p in s.points])
    if len(xy) == 0:
        return None
    (line,) = ax.plot(
        xy[:, 0], xy[:, 1], color=resolve_color(s.color_hint, theme), lw=s.line_width,
        ls=LINESTYLES[s.line_style], zorder=zorder, solid_capstyle="round",
        label=s.label if (legend and s.show_in_legend) else "_nolegend_",
    )
    return line


def label_along(ax, s: CurveSeries, theme: str, frac: float = 0.8, fontsize: float = 7.5,
                color: str | None = None, offset_pts: float = 0.0) -> None:
    """Place ``s.inline_label`` on the curve, rotated to follow its local slope."""
    if not s.inline_label or len(s.points) < 2:
        return
    xy = np.array([[p.x, p.y] for p in s.points])
    x0, x1 = ax.get_xlim()
    y0, y1 = ax.get_ylim()
    inside = (xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)
    xy = xy[inside]
    if len(xy) < 2:
        return
    # arc-length parametrisation in display space for even placement
    disp = ax.transData.transform(xy)
    seg = np.hypot(*np.diff(disp, axis=0).T)
    cum = np.r_[0.0, np.cumsum(seg)]
    if cum[-1] <= 0:
        return
    target = frac * cum[-1]
    i = int(np.clip(np.searchsorted(cum, target), 1, len(xy) - 1))
    t = (target - cum[i - 1]) / max(seg[i - 1], 1e-9)
    px, py = xy[i - 1] + t * (xy[i] - xy[i - 1])
    dx, dy = disp[i] - disp[i - 1]
    angle = math.degrees(math.atan2(dy, dx))
    if angle > 90:
        angle -= 180
    elif angle < -90:
        angle += 180
    p = PALETTE[theme]
    ax.annotate(
        s.inline_label, (px, py), xytext=(0, offset_pts), textcoords="offset points",
        rotation=angle, rotation_mode="anchor", ha="center", va="center", fontsize=fontsize,
        color=color or resolve_color(s.color_hint, theme), zorder=6,
        bbox=dict(boxstyle="round,pad=0.12", fc=p["background"], ec="none", alpha=0.85),
    )


def annotate_point(ax, x: float, y: float, text: str, theme: str, color_token: str = "text",
                   offset=(8, 8), ha="left", va="bottom", marker_color: str | None = None) -> None:
    p = PALETTE[theme]
    mc = resolve_color(marker_color or color_token, theme)
    ax.plot([x], [y], "o", ms=5.5, mfc=mc, mec=p["background"], mew=1.2, zorder=8)
    return ax.annotate(text, (x, y), xytext=offset, textcoords="offset points", ha=ha, va=va,
                fontsize=8.5, color=resolve_color(color_token, theme), zorder=9,
                bbox=dict(boxstyle="round,pad=0.2", fc=p["background"], ec=p["grid"], lw=0.5, alpha=0.9))


def declutter(fig: Figure, annotations: list, pad_px: float = 3.0, max_iter: int = 40) -> None:
    """Nudge overlapping annotation boxes apart vertically (away from their anchor side)."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    canvas = fig.canvas if hasattr(fig.canvas, "get_renderer") else FigureCanvasAgg(fig)
    canvas.draw()
    renderer = canvas.get_renderer()
    pts_per_px = 72.0 / fig.dpi
    axes_box = annotations[0].axes.get_window_extent(renderer) if annotations else None
    for _ in range(max_iter):
        moved = False
        boxes = [a.get_window_extent(renderer) for a in annotations]
        for i in range(len(annotations)):
            for j in range(i):
                bi, bj = boxes[i], boxes[j]
                if bi.x1 < bj.x0 or bj.x1 < bi.x0 or bi.y1 < bj.y0 or bj.y1 < bi.y0:
                    continue
                a = annotations[i]
                dx, dy = a.xyann
                # preferred direction: away from the anchor; fall back to the other side
                # of the obstacle if that would push the box outside the axes
                up_first = a.get_va() == "bottom"
                for up in (up_first, not up_first):
                    shift = ((bj.y1 - bi.y0) if up else (bi.y1 - bj.y0)) + pad_px
                    a.xyann = (dx, dy + (shift if up else -shift) * pts_per_px)
                    nb = a.get_window_extent(renderer)
                    if axes_box is None or (nb.y0 >= axes_box.y0 and nb.y1 <= axes_box.y1):
                        break
                boxes[i] = a.get_window_extent(renderer)
                moved = True
        if not moved:
            break


def subtitle(env: EnvelopeData) -> str:
    fc = env.flight_conditions
    return (f"m = {fc['mass_kg']:,.0f} kg  ·  h = {fc['altitude_ft']:,.0f} ft ({fc['altitude_m']:,.0f} m)  ·  "
            f"ρ = {fc['density_kg_m3']:.4f} kg/m³  ·  W/S = {fc['wing_loading_Pa']:,.0f} Pa  ·  "
            f"{fc['regulation'].upper()} / {fc['category']}")


def finish(fig: Figure, ax, env: EnvelopeData, theme: str) -> None:
    """Title, subtitle, warnings (under the legend) and footer."""
    import textwrap

    p = PALETTE[theme]
    fig.suptitle(env.title, x=0.01, ha="left", fontsize=14, fontweight="bold", color=p["text"])
    ax.set_title(subtitle(env), loc="left", fontsize=8.5, color=p["muted"], pad=8)
    if env.warnings:
        text = "\n".join(textwrap.fill("⚠ " + w, 46, subsequent_indent="   ") for w in env.warnings)
        ax.text(1.015, 0.0, text, transform=ax.transAxes, fontsize=7.2, color=p["amber"],
                ha="left", va="bottom", linespacing=1.3)
    fig.text(0.995, 0.005, "flightenv", ha="right", va="bottom", fontsize=7, color=p["muted"], alpha=0.7)


def save(fig: Figure, path: str | Path, dpi: int = 300) -> Path:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported image format {path.suffix!r}; use one of {sorted(SUPPORTED_FORMATS)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi)
    return path


def legend_outside(ax, handles_first: Iterable = ()) -> None:
    leg = ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), borderaxespad=0.0)
    leg.set_zorder(20)
