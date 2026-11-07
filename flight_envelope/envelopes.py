"""Build the serializable :class:`EnvelopeData` contract from raw engine results.

This is the only place where SI engine output is converted to display units.
"""

from __future__ import annotations

import math

import numpy as np

from .aero.turn import (
    DEFAULT_BANK_ANGLES, TurnResult, aero_load_factor, compute_turn, turn_rate, turn_rate_from_bank,
)
from .aero.vn import VnResult, compute_vn, stall_load_factor
from .models.aircraft import Aircraft
from .models.outputs import (
    AxisSpec, CurveSeries, EnvelopeData, KeyPoint, Point2D, Region, ScalarResult,
    finite_segments, to_points,
)
from .units import FT, LengthUnit, OutputUnits, SpeedType, SpeedUnit

RAD2DEG = 180.0 / math.pi


def _flight_conditions(ac: Aircraft, altitude_m: float, rho: float, sigma: float,
                       a: float | None = None) -> dict:
    from .aero.atmosphere import isa

    atm = isa(altitude_m)
    return {
        "altitude_m": round(altitude_m, 3),
        "altitude_ft": round(altitude_m / FT, 1),
        "temperature_K": round(atm.temperature, 3),
        "pressure_Pa": round(atm.pressure, 2),
        "density_kg_m3": round(rho, 6),
        "density_ratio": round(sigma, 6),
        "speed_of_sound_m_s": round(a if a is not None else atm.speed_of_sound, 3),
        "mass_kg": round(ac.mass, 3),
        "weight_N": round(ac.weight, 2),
        "wing_loading_Pa": round(ac.wing_loading, 3),
        "category": ac.category.value,
        "regulation": ac.regulation_resolved.value,
    }


def _gust_speed_unit(units: OutputUnits) -> tuple[str, float]:
    """Unit used to quote gust velocities: ft/s with imperial speeds, else m/s."""
    return ("ft/s", FT) if units.speed in (SpeedUnit.KT, SpeedUnit.MPH) else ("m/s", 1.0)


# ===========================================================================
# V-n diagram
# ===========================================================================
def build_vn_envelope(r: VnResult, units: OutputUnits | None = None) -> EnvelopeData:
    units = units or OutputUnits()
    ac = r.aircraft
    tas = units.speed_type is SpeedType.TAS

    def sx(v_eas):
        v = np.asarray(v_eas, dtype=float)
        return units.speed_from_si(v / math.sqrt(r.sigma) if tas else v)

    xu, yu = units.speed_label, "g"
    sp = r.speeds
    vd = sp["vd"]
    x_max = float(sx(vd * 1.12))
    y_top = math.ceil(r.n_ult_pos * 1.12 * 2) / 2
    y_bot = math.floor(r.n_ult_neg * 1.15 * 2) / 2

    def stall_v(n):  # EAS where stall curve reaches n, clamped to V_D
        return min(r.stall_speed_for(n), vd)

    def curve(id_, label, x, y, **kw) -> CurveSeries:
        return CurveSeries(id=id_, label=label, x_unit=xu, y_unit=yu, points=to_points(sx(x), y), **kw)

    v = r.v
    # Closed outlines
    def outline(upper, lower):
        xs = np.concatenate([v, [vd], v[::-1]])
        ys = np.concatenate([upper, [lower[-1]], lower[::-1]])
        return xs, ys

    man_x, man_y = outline(r.maneuver_upper, r.maneuver_lower)
    com_x, com_y = outline(r.combined_upper, r.combined_lower)

    g_pts = sorted(r.gust_lines, key=lambda g: g.design_speed)
    gx = np.array([0.0] + [g.design_speed for g in g_pts] + [g.design_speed for g in g_pts[::-1]] + [0.0])
    gy = np.array([1.0] + [1 + g.delta_n for g in g_pts] + [1 - g.delta_n for g in g_pts[::-1]] + [1.0])

    v_stall_ext_p = np.linspace(0.0, min(r.stall_speed_for(y_top), vd * 1.12), 200)
    v_stall_ext_n = np.linspace(0.0, min(r.stall_speed_for(y_bot), vd * 1.12), 200)
    clp = f"{ac.cl_max_clean:.2f}"
    cln = f"{ac.cl_min_clean:.2f}"

    v_ult_p, v_ult_n = stall_v(r.n_ult_pos), stall_v(r.n_ult_neg)
    boundaries = [
        curve("combined_envelope", "Combined V-n envelope (limit)", com_x, com_y,
              role="combined", line_width=1.2, color_hint="text"),
        curve("maneuver_envelope", "Maneuver envelope", man_x, man_y,
              role="maneuver", line_width=2.6, color_hint="cobalt"),
        curve("gust_envelope", "Gust envelope", gx, gy, role="gust_envelope",
              line_style="dashed", line_width=1.8, color_hint="coral"),
        curve("positive_stall", rf"Positive stall ($C_{{L,max}}$ = {clp})", v_stall_ext_p,
              _stall(r, v_stall_ext_p, ac.cl_max_clean), role="stall",
              line_style="dotted", line_width=1.2, color_hint="slate"),
        curve("negative_stall", rf"Negative stall ($C_{{L,min}}$ = {cln})", v_stall_ext_n,
              _stall(r, v_stall_ext_n, ac.cl_min_clean), role="stall",
              line_style="dotted", line_width=1.2, color_hint="slate", show_in_legend=False),
        curve("ultimate_pos", rf"Ultimate load ({ac.ultimate_factor:g}$\times n_{{limit}}$)",
              [v_ult_p, vd], [r.n_ult_pos] * 2, role="ultimate", line_style="dashed",
              line_width=1.4, color_hint="crimson"),
        curve("ultimate_neg", "Ultimate load (negative)", [v_ult_n, vd], [r.n_ult_neg] * 2,
              role="ultimate", line_style="dashed", line_width=1.4, color_hint="crimson",
              show_in_legend=False),
    ]

    gu, gf = _gust_speed_unit(units)
    contours = []
    for i, g in enumerate(g_pts):
        name = {"vb": "V_B", "vc": "V_C", "vd": "V_D"}[g.key]
        txt = rf"$\pm${g.u_de / gf:.4g} {gu} (${name}$)"
        x = np.array([0.0, vd])
        for sign, suffix in ((1, "pos"), (-1, "neg")):
            contours.append(curve(
                f"gust_{g.key}_{suffix}", rf"Gust lines $U_{{de}}$" if (i == 0 and sign > 0) else txt,
                x, 1.0 + sign * g.slope * x, role="gust", line_style="dashed", line_width=0.8,
                color_hint="amber", inline_label=txt if sign > 0 else None,
                show_in_legend=(i == 0 and sign > 0)))
    contours.append(curve("level_flight", "1 g", [0.0, vd * 1.12], [1.0, 1.0], role="reference",
                          line_style="dotted", line_width=0.8, color_hint="muted",
                          show_in_legend=False))

    # ----------------------------------------------------------- regions
    def reg(id_, label, x, y, color, opacity, role="", hatch=None, legend=True) -> Region:
        return Region(id=id_, label=label, role=role, points=to_points(sx(x), y),
                      color_hint=color, opacity=opacity, hatch=hatch, show_in_legend=legend)

    vx = np.linspace(0.0, vd * 1.12, 300)
    sp_c = np.minimum(_stall(r, vx, ac.cl_max_clean), y_top)
    sn_c = np.maximum(_stall(r, vx, ac.cl_min_clean), y_bot)
    regions = [
        reg("safe_envelope", "Permissible flight envelope", com_x, com_y, "safe", 0.12, "safe"),
        reg("stall_pos", "Aerodynamic stall region", np.r_[vx, vx[-1], 0.0], np.r_[sp_c, y_top, y_top],
            "slate", 0.10, "stall"),
        reg("stall_neg", "Aerodynamic stall region (negative)", np.r_[vx, vx[-1], 0.0],
            np.r_[sn_c, y_bot, y_bot], "slate", 0.10, "stall", legend=False),
    ]
    for sign, nd, nu, edge in ((1, r.n_design_pos, r.n_ult_pos, y_top), (-1, r.n_design_neg, r.n_ult_neg, y_bot)):
        suffix = "pos" if sign > 0 else "neg"
        cl = ac.cl_max_clean if sign > 0 else ac.cl_min_clean
        vs_d, vs_u, vs_e = stall_v(nd), stall_v(nu), stall_v(edge)
        arc_du = np.linspace(vs_u, vs_d, 40)
        arc_ue = np.linspace(vs_e, vs_u, 40)
        regions.append(reg(f"structural_damage_{suffix}",
                           "Structural damage (limit → ultimate)" if sign > 0 else "Structural damage (negative)",
                           np.r_[vs_d, vd, vd, arc_du], np.r_[nd, nd, nu, _stall(r, arc_du, cl)],
                           "amber", 0.14, "structural_damage", legend=sign > 0))
        regions.append(reg(f"structural_failure_{suffix}",
                           "Structural failure (> ultimate)" if sign > 0 else "Structural failure (negative)",
                           np.r_[vs_u, vd, vd, arc_ue], np.r_[nu, nu, edge, np.clip(_stall(r, arc_ue, cl), y_bot, y_top)],
                           "crimson", 0.16, "structural_failure", legend=sign > 0))
    regions.append(reg("overspeed", r"Overspeed (> $V_D$)", [vd, vd * 1.12, vd * 1.12, vd],
                       [y_bot, y_bot, y_top, y_top], "crimson", 0.08, "overspeed"))

    # ----------------------------------------------------------- key points
    def up(vq):
        return float(np.interp(vq, v, r.combined_upper))

    def lo(vq):
        return float(np.interp(vq, v, r.combined_lower))

    kp = {
        "Vs1": (sp["vs1"], 1.0, r"$V_{S1}$", "1 g stall speed"),
        "Vs_neg": (sp["vs_neg"], -1.0, r"$V_{S,inv}$", "-1 g (inverted) stall speed"),
        "Va": (sp["va"], r.n_pos, r"$V_A$", "Design maneuvering (corner) speed"),
        "Vg": (sp["vg"], r.n_neg, r"$V_G$", "Negative maneuvering corner speed"),
        "Vc_pos": (sp["vc"], up(sp["vc"]), r"$V_C$", "Design cruise speed (positive limit)"),
        "Vc_neg": (sp["vc"], lo(sp["vc"]), r"$V_C$", "Design cruise speed (negative limit)"),
        "Vd_pos": (vd, up(vd), r"$V_D$", "Design dive speed (positive limit)"),
        "Vd_neg": (vd, lo(vd), r"$V_D$", "Design dive speed (negative limit)"),
    }
    if "vb" in sp:
        kp["Vb"] = (sp["vb"], up(sp["vb"]), r"$V_B$", "Design speed for maximum gust intensity")
    key_points = {k: KeyPoint(x=round(float(sx(x)), 4), y=round(y, 4), label=lbl, description=d)
                  for k, (x, y, lbl, d) in kp.items()}

    def s(val, unit, label, desc=""):
        return ScalarResult(value=round(float(val), 5), unit=unit, label=label, description=desc)

    summary = {
        "vs1": s(sx(sp["vs1"]), xu, "V_S1", "Stall speed, 1 g"),
        "va": s(sx(sp["va"]), xu, "V_A", "Maneuvering speed"),
        "vg": s(sx(sp["vg"]), xu, "V_G", "Negative maneuvering speed"),
        "vc": s(sx(sp["vc"]), xu, "V_C", "Design cruise speed"),
        "vd": s(sx(vd), xu, "V_D", "Design dive speed"),
        "n_pos_limit": s(r.n_pos, "g", "n_pos", f"Positive maneuver limit ({r.limit_sources[0]})"),
        "n_neg_limit": s(r.n_neg, "g", "n_neg", f"Negative maneuver limit ({r.limit_sources[1]})"),
        "n_design_pos": s(r.n_design_pos, "g", "n_design+", "Positive design limit (maneuver ∪ gust)"),
        "n_design_neg": s(r.n_design_neg, "g", "n_design-", "Negative design limit (maneuver ∪ gust)"),
        "n_ult_pos": s(r.n_ult_pos, "g", "n_ult+", "Positive ultimate load factor"),
        "n_ult_neg": s(r.n_ult_neg, "g", "n_ult-", "Negative ultimate load factor"),
        "mu_g": s(r.mu_g, "-", "mu_g", "Airplane gust mass ratio"),
        "k_g": s(r.k_g, "-", "K_g", "Gust alleviation factor"),
    }
    if "vb" in sp:
        summary["vb"] = s(sx(sp["vb"]), xu, "V_B", "Design speed for max gust intensity")
    for g in g_pts:
        summary[f"ude_{g.key}"] = s(g.u_de / gf, gu, f"U_de({g.key.upper()})", "Derived gust velocity (EAS)")
        summary[f"n_gust_{g.key}"] = s(1 + g.delta_n, "g", f"n_gust({g.key.upper()})", "Positive gust load factor")

    alt_txt = f"{r.altitude_m / FT:,.0f} ft"
    return EnvelopeData(
        envelope_type="vn_diagram",
        aircraft_name=ac.name,
        title=f"V-n Diagram — {ac.name}",
        flight_conditions=_flight_conditions(ac, r.altitude_m, r.rho, r.sigma),
        units={"speed": xu, "load_factor": "g", "gust_velocity": gu},
        x_axis=AxisSpec(label=f"Airspeed [{xu}]", unit=xu, min=0.0, max=x_max),
        y_axis=AxisSpec(label="Load factor n [g]", unit="g", min=y_bot, max=y_top),
        key_points=key_points,
        summary=summary,
        boundaries=boundaries,
        contours=contours,
        regions=regions,
        safe_region_polygon=to_points(sx(com_x), com_y),
        warnings=list(r.warnings) + ([f"Speeds shown as TAS at {alt_txt}"] if tas else []),
    )


def _stall(r: VnResult, v_eas, cl: float):
    """Stall-curve load factor at EAS ``v_eas`` for lift coefficient ``cl``."""
    return stall_load_factor(v_eas, r.aircraft, cl)


# ===========================================================================
# Turn performance
# ===========================================================================
_RADII = {LengthUnit.M: [50, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 20000],
          LengthUnit.FT: [200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000, 7500, 10000, 15000, 20000, 30000, 50000]}


def auto_radii(r_min_disp: float, unit: LengthUnit, count: int = 4) -> list[float]:
    cands = [c for c in _RADII[unit] if c >= r_min_disp * 1.02]
    return [float(c) for c in cands[:count]]


def build_turn_envelope(
    r: TurnResult,
    units: OutputUnits | None = None,
    bank_angles_deg: list[float] | None = None,
    radii: list[float] | None = None,
) -> EnvelopeData:
    """Doghouse plot data. ``radii`` are in ``units.length``."""
    units = units or OutputUnits(speed_type=SpeedType.TAS)
    ac = r.aircraft
    eas = units.speed_type is SpeedType.EAS
    root_s = math.sqrt(r.sigma)

    def sx(v_tas):
        v = np.asarray(v_tas, dtype=float)
        return units.speed_from_si(v * root_s if eas else v)

    xu, yu = units.speed_label, "deg/s"
    lu = units.length.value
    v = r.v
    om_inst = r.omega_inst * RAD2DEG
    x_min_si, x_max_si = 0.8 * r.v_stall_1g, 1.08 * r.v_max
    y_top = float(np.ceil(r.omega_max_inst * RAD2DEG * 1.22))

    def curve(id_, label, x, y, **kw) -> CurveSeries:
        return CurveSeries(id=id_, label=label, x_unit=xu, y_unit=yu, points=to_points(sx(x), y), **kw)

    def clip_top(y):
        y = np.asarray(y, dtype=float)
        return np.where(y <= y_top, y, np.nan)

    boundaries: list[CurveSeries] = []
    house_x = np.r_[v, r.v_max]
    house_y = np.r_[om_inst, 0.0]
    boundaries.append(curve("instantaneous_envelope", "Instantaneous turn envelope", house_x, house_y,
                            role="instantaneous", line_width=2.6, color_hint="cobalt"))

    v_ext = np.linspace(r.v_stall_1g, x_max_si, 500)
    om_aero = clip_top(turn_rate(v_ext, aero_load_factor(v_ext, r.rho, ac)) * RAD2DEG)
    for xs, ys in finite_segments(v_ext, om_aero)[:1]:
        boundaries.append(curve("aerodynamic_limit", rf"Stall limit ($C_{{L,max}}$ = {ac.cl_max_clean:.2f})",
                                xs, ys, role="stall", line_style="dashed", line_width=1.1, color_hint="cobalt"))
    v_st = np.linspace(x_min_si, x_max_si, 400)
    om_st = clip_top(turn_rate(v_st, np.full_like(v_st, r.n_limit)) * RAD2DEG)
    for xs, ys in finite_segments(v_st, om_st)[:1]:
        boundaries.append(curve("structural_limit", f"Structural limit (n = {r.n_limit:.2f} g)", xs, ys,
                                role="structural", line_style="dashed", line_width=1.3, color_hint="crimson"))
    for i, (xs, ys) in enumerate(finite_segments(v, r.omega_sustained * RAD2DEG)):
        boundaries.append(curve("sustained_limit" if i == 0 else f"sustained_limit_{i}",
                                r"Sustained turn ($P_s$ = 0)", xs, ys, role="sustained",
                                line_width=2.4, color_hint="teal", show_in_legend=(i == 0)))
    boundaries.append(curve("q_limit", r"$V_D$ (q-limit)", [r.v_max, r.v_max], [0.0, y_top], role="q_limit",
                            line_style="dotted", line_width=1.0, color_hint="crimson", show_in_legend=False))

    # ---------------------------------------------------------- contours
    contours: list[CurveSeries] = []
    v_c = np.linspace(x_min_si, x_max_si, 400)
    for i, phi in enumerate(bank_angles_deg or list(DEFAULT_BANK_ANGLES)):
        om = clip_top(turn_rate_from_bank(v_c, math.radians(phi)) * RAD2DEG)
        for xs, ys in finite_segments(v_c, om)[:1]:
            n = 1 / math.cos(math.radians(phi))
            contours.append(curve(f"bank_{phi:g}", "Constant bank angle φ" if i == 0 else f"φ = {phi:g}°",
                                  xs, ys, role="bank_angle", line_style="dotted", line_width=0.8,
                                  color_hint="muted", inline_label=f"{phi:g}° ({n:.2g} g)",
                                  show_in_legend=(i == 0)))
    r_min_disp = units.length_from_si(r.radius_min)
    for i, rad in enumerate(radii or auto_radii(r_min_disp, units.length)):
        om = clip_top(v_c / units.length_to_si(rad) * RAD2DEG)
        for xs, ys in finite_segments(v_c, om)[:1]:
            contours.append(curve(f"radius_{rad:g}{lu}", "Constant turn radius R" if i == 0 else f"R = {rad:g} {lu}",
                                  xs, ys, role="radius", line_style="dashdot", line_width=0.8,
                                  color_hint="violet", inline_label=f"R = {rad:,.0f} {lu}",
                                  show_in_legend=(i == 0)))
    ps_unit, ps_f = (f"{lu}/s", FT) if units.length is LengthUnit.FT else ("m/s", 1.0)
    for i, (ps, om) in enumerate(sorted(r.ps_contours.items(), reverse=True)):
        for j, (xs, ys) in enumerate(finite_segments(v, om * RAD2DEG)):
            txt = rf"$P_s$ = {ps / ps_f:,.0f} {ps_unit}"
            contours.append(curve(f"ps_{ps / ps_f:.4g}" + (f"_{j}" if j else ""),
                                  r"Specific excess power $P_s$" if i == 0 else txt, xs, ys,
                                  role="ps", line_width=1.0, color_hint="amber", inline_label=txt,
                                  show_in_legend=(i == 0 and j == 0)))

    # ---------------------------------------------------------- regions
    def reg(id_, label, x, y, color, opacity, role=""):
        return Region(id=id_, label=label, role=role, points=to_points(sx(x), y),
                      color_hint=color, opacity=opacity)

    sus = np.nan_to_num(r.omega_sustained * RAD2DEG, nan=0.0)
    sus = np.minimum(sus, om_inst)
    regions = [
        reg("safe_envelope", "Instantaneous turn capability", house_x, house_y, "safe", 0.08, "safe"),
        reg("energy_depletion", "Energy bleed zone (Ps < 0)", np.r_[v, v[::-1]], np.r_[om_inst, sus[::-1]],
            "amber", 0.20, "energy_depletion"),
    ]
    aero_seg = finite_segments(v_ext, om_aero)
    if aero_seg:
        ax_, ay_ = aero_seg[0]
        regions.append(reg("stall_region", "Stall region", np.r_[x_min_si, ax_, ax_[-1], x_min_si],
                           np.r_[0.0, ay_, y_top, y_top], "slate", 0.08, "stall"))
        if r.v_corner < r.v_max:
            mask = (v_st >= r.v_corner) & (v_st <= r.v_max)
            sx_, sy_ = v_st[mask], np.nan_to_num(om_st[mask], nan=y_top)
            a_mask = (ax_ >= r.v_corner)
            regions.append(reg("overstress", "Structural overstress", np.r_[r.v_corner, sx_, r.v_max, ax_[a_mask][::-1]],
                               np.r_[r.omega_max_inst * RAD2DEG, sy_, y_top, ay_[a_mask][::-1]],
                               "crimson", 0.08, "structural_failure"))
    regions.append(reg("overspeed", r"Beyond $V_D$", [r.v_max, x_max_si, x_max_si, r.v_max],
                       [0, 0, y_top, y_top], "crimson", 0.07, "overspeed"))

    # ---------------------------------------------------------- key points
    ic = int(np.argmax(r.omega_inst))
    key_points = {
        "corner": KeyPoint(x=round(float(sx(v[ic])), 4), y=round(float(om_inst[ic]), 4), label=r"$V^*$",
                           description="Corner speed: maximum instantaneous turn rate / minimum radius"),
        "stall_1g": KeyPoint(x=round(float(sx(r.v_stall_1g)), 4), y=0.0, label=r"$V_{S}$",
                             description="1 g stall speed"),
        "v_max": KeyPoint(x=round(float(sx(r.v_max)), 4), y=round(float(om_inst[-1]), 4), label=r"$V_D$",
                          description="Dive speed (dynamic pressure limit)"),
    }
    if r.omega_max_sus > 0:
        key_points["max_sustained"] = KeyPoint(
            x=round(float(sx(r.v_max_sus)), 4), y=round(r.omega_max_sus * RAD2DEG, 4),
            label=r"$\omega_{sus,max}$", description="Maximum sustained turn rate")

    def s(val, unit, label, desc=""):
        return ScalarResult(value=round(float(val), 5), unit=unit, label=label, description=desc)

    summary = {
        "v_stall_1g": s(sx(r.v_stall_1g), xu, "V_S", "1 g stall speed"),
        "v_corner": s(sx(r.v_corner), xu, "V*", "Corner speed"),
        "mach_corner": s(r.v_corner / r.speed_of_sound, "-", "M*", "Corner Mach number"),
        "omega_max_inst": s(r.omega_max_inst * RAD2DEG, "deg/s", "ω_inst,max", "Max instantaneous turn rate"),
        "radius_min": s(units.length_from_si(r.radius_min), lu, "R_min", "Minimum turn radius"),
        "n_limit": s(r.n_limit, "g", "n_limit", "Structural limit load factor"),
        "omega_max_sus": s(r.omega_max_sus * RAD2DEG, "deg/s", "ω_sus,max", "Max sustained turn rate"),
        "v_max_sus": s(sx(r.v_max_sus), xu, "V(ω_sus,max)", "Speed for max sustained turn rate"),
        "n_max_sus": s(r.n_max_sus, "g", "n_sus", "Load factor at max sustained turn rate"),
        "v_max": s(sx(r.v_max), xu, "V_D", "Dynamic pressure (dive speed) limit"),
    }
    if r.v_level_range:
        summary["v_level_min"] = s(sx(r.v_level_range[0]), xu, "V_min,level", "Min level-flight speed (T = D, within grid)")
        summary["v_level_max"] = s(sx(r.v_level_range[1]), xu, "V_max,level", "Max level-flight speed (T = D, within grid)")

    fc = _flight_conditions(ac, r.altitude_m, r.rho, r.sigma, r.speed_of_sound)
    fc["speed_of_sound_display"] = round(float(units.speed_from_si(r.speed_of_sound)), 4)
    fc["mach_axis_valid"] = not eas
    safe = to_points(sx(house_x), house_y)
    return EnvelopeData(
        envelope_type="turn_performance",
        aircraft_name=ac.name,
        title=f"Turn Performance — {ac.name}",
        flight_conditions=fc,
        units={"speed": xu, "turn_rate": yu, "radius": lu, "specific_excess_power": ps_unit},
        x_axis=AxisSpec(label=f"Airspeed [{xu}]", unit=xu, min=float(sx(x_min_si)), max=float(sx(x_max_si))),
        y_axis=AxisSpec(label="Turn rate ω [deg/s]", unit=yu, min=0.0, max=y_top),
        key_points=key_points,
        summary=summary,
        boundaries=boundaries,
        contours=contours,
        regions=regions,
        safe_region_polygon=safe + [safe[0]] if safe else [],
        warnings=list(r.warnings),
    )


# ===========================================================================
# High-level API
# ===========================================================================
def vn_envelope(ac: Aircraft, altitude_m: float = 0.0, units: OutputUnits | None = None,
                n_points: int = 600) -> tuple[VnResult, EnvelopeData]:
    res = compute_vn(ac, altitude_m, n_points=n_points)
    return res, build_vn_envelope(res, units)


def turn_envelope(ac: Aircraft, altitude_m: float = 0.0, units: OutputUnits | None = None,
                  bank_angles_deg: list[float] | None = None, radii: list[float] | None = None,
                  ps_levels: list[float] | None = None, n_points: int = 600) -> tuple[TurnResult, EnvelopeData]:
    units = units or OutputUnits(speed_type=SpeedType.TAS)
    res = compute_turn(ac, altitude_m, ps_levels=ps_levels, n_points=n_points,
                       ps_unit=units.length_to_si(1.0))
    return res, build_turn_envelope(res, units, bank_angles_deg, radii)


__all__ = ["build_vn_envelope", "build_turn_envelope", "vn_envelope", "turn_envelope", "Point2D"]
