"""V-n diagram renderer (consumes :class:`EnvelopeData` only)."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator

from ..models.outputs import EnvelopeData
from ..palette import PALETTE
from . import style

# (dx, dy, ha, va) offsets in points for key-point annotations
_OFFSETS = {
    "Vs1": (10, -14, "left", "top"),
    "Vs_neg": (10, 10, "left", "bottom"),
    "Va": (-10, 10, "right", "bottom"),
    "Vg": (-10, -10, "right", "top"),
    "Vb": (12, -8, "left", "top"),
    "Vc_pos": (0, 14, "center", "bottom"),
    "Vc_neg": (0, -14, "center", "top"),
    "Vd_pos": (-10, 10, "right", "bottom"),
    "Vd_neg": (-10, -10, "right", "top"),
}
_POINT_COLORS = {"Vs1": "slate", "Vs_neg": "slate", "Va": "cobalt", "Vg": "cobalt",
                 "Vb": "coral", "Vc_pos": "text", "Vc_neg": "text", "Vd_pos": "crimson", "Vd_neg": "crimson"}


def render_vn(env: EnvelopeData, theme: str = "light") -> Figure:
    if env.envelope_type != "vn_diagram":
        raise ValueError("render_vn expects a 'vn_diagram' envelope")
    with mpl.rc_context(style.rc_params(theme)):
        fig = style.new_figure(theme)
        ax = fig.axes[0]
        p = PALETTE[theme]

        order = {"safe": 0.4, "stall": 0.6, "structural_damage": 0.5, "structural_failure": 0.5, "overspeed": 0.45}
        for reg in env.regions:
            style.draw_region(ax, reg, theme, zorder=order.get(reg.role, 0.5))

        ax.axhline(0.0, color=p["muted"], lw=0.8, zorder=1)
        for s in env.contours or []:
            style.draw_series(ax, s, theme, zorder=1.5)
        z = {"combined": 3.2, "maneuver": 3.0, "gust_envelope": 2.8, "stall": 2.2, "ultimate": 2.4}
        for s in env.boundaries:
            style.draw_series(ax, s, theme, zorder=z.get(s.role, 2.0))

        ax.set_xlim(env.x_axis.min, env.x_axis.max)
        ax.set_ylim(env.y_axis.min, env.y_axis.max)
        ax.set_xlabel(env.x_axis.label)
        ax.set_ylabel(env.y_axis.label)
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())

        # Gust line labels near their right-hand end, inside the plot
        for s in env.contours or []:
            if s.role == "gust" and s.inline_label:
                style.label_along(ax, s, theme, frac=0.93, color=p["amber"], offset_pts=6)

        xu = env.x_axis.unit
        notes = []
        for key, kp in env.key_points.items():
            dx, dy, ha, va = _OFFSETS.get(key, (8, 8, "left", "bottom"))
            text = f"{kp.label}  {kp.x:,.0f} {xu}" if not key.startswith("Vs") else f"{kp.label}  {kp.x:,.0f}"
            if key in ("Va", "Vg", "Vc_pos", "Vc_neg", "Vd_pos", "Vd_neg", "Vb"):
                text = f"{kp.label}\n{kp.x:,.0f} {xu} · {kp.y:+.2f} g"
            notes.append(style.annotate_point(ax, kp.x, kp.y, text, theme, color_token="text",
                                 marker_color=_POINT_COLORS.get(key, "text"), offset=(dx, dy), ha=ha, va=va))

        # Region captions
        def caption(rid, x, y, text, color):
            try:
                env.region(rid)
            except KeyError:
                return
            ax.text(x, y, text, fontsize=8, color=color, alpha=0.9, ha="center", va="center", zorder=7,
                    style="italic")

        xr = env.x_axis.max
        n_ult = env.summary["n_ult_pos"].value
        caption("structural_failure_pos", 0.62 * xr, (n_ult + env.y_axis.max) / 2, "STRUCTURAL FAILURE", p["crimson"])
        caption("stall_pos", 0.07 * xr, 0.55 * env.y_axis.max, "STALL", p["slate"])

        style.legend_outside(ax)
        style.finish(fig, ax, env, theme)
        style.declutter(fig, notes)
    return fig


def plot_vn(env: EnvelopeData, path: str | Path, theme: str = "light", dpi: int = 300) -> Path:
    return style.save(render_vn(env, theme), path, dpi)
