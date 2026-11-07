"""Turn-performance (doghouse / E-M) renderer (consumes :class:`EnvelopeData` only)."""

from __future__ import annotations

from pathlib import Path

import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.ticker import AutoMinorLocator

from ..models.outputs import EnvelopeData
from ..palette import PALETTE
from . import style


def render_turn(env: EnvelopeData, theme: str = "light") -> Figure:
    if env.envelope_type != "turn_performance":
        raise ValueError("render_turn expects a 'turn_performance' envelope")
    with mpl.rc_context(style.rc_params(theme)):
        fig = style.new_figure(theme)
        ax = fig.axes[0]
        p = PALETTE[theme]

        order = {"safe": 0.4, "energy_depletion": 0.6, "stall": 0.5, "structural_failure": 0.5, "overspeed": 0.45}
        for reg in env.regions:
            style.draw_region(ax, reg, theme, zorder=order.get(reg.role, 0.5))

        for s in env.contours or []:
            style.draw_series(ax, s, theme, zorder=1.5)
        z = {"instantaneous": 3.2, "sustained": 3.0, "structural": 2.4, "stall": 2.2}
        for s in env.boundaries:
            style.draw_series(ax, s, theme, zorder=z.get(s.role, 2.0))

        ax.set_xlim(env.x_axis.min, env.x_axis.max)
        ax.set_ylim(env.y_axis.min, env.y_axis.max)
        ax.set_xlabel(env.x_axis.label)
        ax.set_ylabel(env.y_axis.label)
        ax.xaxis.set_minor_locator(AutoMinorLocator())
        ax.yaxis.set_minor_locator(AutoMinorLocator())

        for s in env.contours or []:
            if s.role == "bank_angle":
                style.label_along(ax, s, theme, frac=0.12, color=p["muted"])
            elif s.role == "radius":
                style.label_along(ax, s, theme, frac=0.82, color=p["violet"])
            elif s.role == "ps":
                style.label_along(ax, s, theme, frac=0.62, color=p["amber"], offset_pts=0)

        fc = env.flight_conditions
        if fc.get("mach_axis_valid") and fc.get("speed_of_sound_display"):
            a = fc["speed_of_sound_display"]
            sec = ax.secondary_xaxis("top", functions=(lambda x: x / a, lambda m: m * a))
            sec.set_xlabel("Mach number", fontsize=9, color=p["muted"])
            sec.tick_params(labelsize=8, colors=p["muted"])

        xu = env.x_axis.unit
        kp = env.key_points
        sm = env.summary
        lu = env.units.get("radius", "m")
        notes = []
        if "corner" in kp:
            c = kp["corner"]
            notes.append(style.annotate_point(
                ax, c.x, c.y,
                f"Corner {c.label} = {c.x:,.0f} {xu}\n"
                f"$\\omega_{{max}}$ = {c.y:.1f}°/s · $R_{{min}}$ = {sm['radius_min'].value:,.0f} {lu}",
                theme, marker_color="cobalt", offset=(-14, 6), ha="right", va="bottom"))
        if "max_sustained" in kp:
            m = kp["max_sustained"]
            notes.append(style.annotate_point(
                ax, m.x, m.y, f"Max sustained {m.y:.1f}°/s\n@ {m.x:,.0f} {xu}, n = {sm['n_max_sus'].value:.2f} g",
                theme, marker_color="teal", offset=(10, -10), ha="left", va="top"))
        if "stall_1g" in kp:
            s1 = kp["stall_1g"]
            style.annotate_point(ax, s1.x, s1.y, f"{s1.label} {s1.x:,.0f}", theme, marker_color="slate",
                                 offset=(6, 8), ha="left", va="bottom")
        if "v_max" in kp:
            vm = kp["v_max"]
            ax.text(vm.x, env.y_axis.max * 0.97, f" {vm.label} {vm.x:,.0f} {xu}", fontsize=8,
                    color=p["crimson"], ha="left", va="top", rotation=90, zorder=7)

        style.legend_outside(ax)
        style.finish(fig, ax, env, theme)
        style.declutter(fig, notes)
    return fig


def plot_turn(env: EnvelopeData, path: str | Path, theme: str = "light", dpi: int = 300) -> Path:
    return style.save(render_turn(env, theme), path, dpi)
