r"""V-n diagram engine: maneuver envelope, gust envelope and combined boundary.

References
----------
* 14 CFR 23.333, 23.335, 23.337, 23.341 (pre-Amdt. 23-64 numerical criteria)
* 14 CFR 25.333, 25.335, 25.337, 25.341 (Pratt-formula discrete gust, pre-Amdt. 25-86)
* Raymer, *Aircraft Design: A Conceptual Approach*, sec. 14.2

All speeds are equivalent airspeeds (EAS) in m/s. The maneuver envelope is
altitude-independent in EAS; altitude enters through the gust mass ratio
:math:`\mu_g` (actual density) and the altitude reduction of :math:`U_{de}`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..models.aircraft import Aircraft, Category, Regulation
from ..units import FT, G0, LBF
from .atmosphere import RHO0, isa

LIMIT_SOURCE_USER = "user"


# ---------------------------------------------------------------------------
# Structural limit load factors
# ---------------------------------------------------------------------------
def far_positive_limit(weight_lbf: float, category: Category) -> float:
    """Minimum positive limit maneuvering load factor, 14 CFR 23.337(a) / 25.337(b).

    Normal/commuter/transport: n = 2.1 + 24 000 / (W + 10 000), not less than 2.5
    and need not exceed 3.8.  Utility: 4.4.  Aerobatic: 6.0.
    """
    if category is Category.UTILITY:
        return 4.4
    if category is Category.AEROBATIC:
        return 6.0
    if category is Category.MILITARY:
        return 7.33  # MIL-A-8861 fighter/attack basic design value
    n = 2.1 + 24_000.0 / (weight_lbf + 10_000.0)
    return float(min(max(n, 2.5), 3.8))


def far_negative_limit(n_pos: float, category: Category) -> float:
    """Negative limit load factor, 14 CFR 23.337(b) / 25.337(c)."""
    if category is Category.TRANSPORT:
        return -1.0
    if category is Category.AEROBATIC:
        return -0.5 * n_pos
    if category is Category.MILITARY:
        return -3.0
    return -0.4 * n_pos


def resolve_limits(ac: Aircraft) -> tuple[float, float, str, str]:
    """Return (n_pos, n_neg, source_pos, source_neg)."""
    w_lbf = ac.weight / LBF
    if ac.n_pos_limit is not None:
        n_pos, src_p = ac.n_pos_limit, LIMIT_SOURCE_USER
    else:
        n_pos = far_positive_limit(w_lbf, ac.category)
        src_p = "MIL-A-8861" if ac.category is Category.MILITARY else (
            "14 CFR 25.337(b)" if ac.category is Category.TRANSPORT else "14 CFR 23.337(a)")
    if ac.n_neg_limit is not None:
        n_neg, src_n = ac.n_neg_limit, LIMIT_SOURCE_USER
    else:
        n_neg = far_negative_limit(n_pos, ac.category)
        src_n = "MIL-A-8861" if ac.category is Category.MILITARY else (
            "14 CFR 25.337(c)" if ac.category is Category.TRANSPORT else "14 CFR 23.337(b)")
    return float(n_pos), float(n_neg), src_p, src_n


def negative_limit_at_vd(ac: Aircraft, n_neg: float) -> float:
    """Negative maneuver limit at V_D (23.333(b)(2) / 25.333(b))."""
    if ac.n_neg_at_vd is not None:
        return ac.n_neg_at_vd
    if ac.category in (Category.UTILITY, Category.AEROBATIC, Category.MILITARY):
        return max(-1.0, n_neg)
    return 0.0


# ---------------------------------------------------------------------------
# Gust model
# ---------------------------------------------------------------------------
def gust_mass_ratio(wing_loading: float, rho: float, chord: float, cl_alpha: float) -> float:
    r"""Airplane mass ratio :math:`\mu_g = 2 (W/S) / (\rho \bar c g C_{L\alpha})`."""
    return 2.0 * wing_loading / (rho * chord * G0 * cl_alpha)


def gust_alleviation_factor(mu_g: float) -> float:
    r"""Pratt gust alleviation factor :math:`K_g = 0.88 \mu_g / (5.3 + \mu_g)`."""
    return 0.88 * mu_g / (5.3 + mu_g)


def gust_increment(v_eas, u_de: float, wing_loading: float, cl_alpha: float, k_g: float):
    r""":math:`\Delta n = \rho_0 V_e C_{L\alpha} K_g U_{de} / (2 W/S)`."""
    return RHO0 * np.asarray(v_eas) * cl_alpha * k_g * u_de / (2.0 * wing_loading)


def _altitude_scaled(u_low: float, u_50k: float, altitude_m: float) -> float:
    h_ft = altitude_m / FT
    if h_ft <= 20_000.0:
        return u_low
    if h_ft >= 50_000.0:
        return u_50k
    return u_low + (u_50k - u_low) * (h_ft - 20_000.0) / 30_000.0


def derived_gust_velocities(ac: Aircraft, altitude_m: float) -> dict[str, float]:
    g = ac.gust
    return {
        "vb": _altitude_scaled(g.u_b, g.u_b_50k, altitude_m),
        "vc": _altitude_scaled(g.u_c, g.u_c_50k, altitude_m),
        "vd": _altitude_scaled(g.u_d, g.u_d_50k, altitude_m),
    }


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GustLine:
    key: str  # "vb" | "vc" | "vd"
    u_de: float  # [m/s EAS]
    slope: float  # dn/dV  [1/(m/s)]
    design_speed: float  # [m/s EAS]

    @property
    def delta_n(self) -> float:
        return self.slope * self.design_speed


@dataclass(frozen=True)
class VnResult:
    """Raw SI output of the V-n engine (speeds are EAS m/s)."""

    aircraft: Aircraft
    altitude_m: float
    rho: float
    sigma: float
    regulation: Regulation

    n_pos: float
    n_neg: float
    n_neg_vd: float
    limit_sources: tuple[str, str]
    n_design_pos: float  # max of maneuver/gust limit loads
    n_design_neg: float
    n_ult_pos: float
    n_ult_neg: float

    speeds: dict[str, float]  # vs1, vs_neg, va, vg, vb?, vc, vd
    mu_g: float
    k_g: float
    gust_lines: list[GustLine]

    v: np.ndarray
    stall_pos: np.ndarray
    stall_neg: np.ndarray
    maneuver_upper: np.ndarray
    maneuver_lower: np.ndarray
    gust_upper: np.ndarray
    gust_lower: np.ndarray
    combined_upper: np.ndarray
    combined_lower: np.ndarray
    warnings: list[str] = field(default_factory=list)

    @property
    def gust_governs_positive(self) -> bool:
        return self.n_design_pos > self.n_pos + 1e-9

    @property
    def gust_governs_negative(self) -> bool:
        return self.n_design_neg < self.n_neg - 1e-9

    def stall_speed_for(self, n: float) -> float:
        """EAS at which the stall curve reaches load factor ``n`` (sign selects branch)."""
        ac = self.aircraft
        cl = ac.cl_max_clean if n > 0 else ac.cl_min_clean
        return math.sqrt(2.0 * ac.weight * n / (RHO0 * ac.wing_area * cl))


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
def stall_load_factor(v_eas, ac: Aircraft, cl: float):
    """n = 1/2 rho0 V_e^2 S C_L / W."""
    return 0.5 * RHO0 * np.asarray(v_eas) ** 2 * ac.wing_area * cl / ac.weight


def compute_vn(ac: Aircraft, altitude_m: float = 0.0, n_points: int = 600) -> VnResult:
    """Compute the maneuver, gust and combined V-n envelopes."""
    atm = isa(altitude_m)
    rho, sigma = atm.density, atm.sigma
    reg = ac.regulation_resolved
    warnings: list[str] = []

    n_pos, n_neg, src_p, src_n = resolve_limits(ac)
    n_neg_vd = negative_limit_at_vd(ac, n_neg)
    ws, S, W = ac.wing_loading, ac.wing_area, ac.weight
    vc, vd = ac.v_c, ac.v_d_resolved

    # --- characteristic speeds --------------------------------------------
    vs1 = math.sqrt(2.0 * W / (RHO0 * S * ac.cl_max_clean))
    vs_neg = math.sqrt(2.0 * W / (RHO0 * S * abs(ac.cl_min_clean)))
    va = vs1 * math.sqrt(n_pos)
    vg = vs_neg * math.sqrt(abs(n_neg))
    if vd < 1.25 * vc - 1e-9 and reg is Regulation.FAR23:
        warnings.append(f"V_D = {vd:.1f} m/s is below 1.25 V_C ({1.25 * vc:.1f} m/s), "
                        "cf. 14 CFR 23.335(b)")
    if va > vc:
        warnings.append(f"V_A ({va:.1f} m/s) exceeds V_C ({vc:.1f} m/s); "
                        "14 CFR 23.335(c) allows V_A to be limited to V_C")
    if vg > vc:
        warnings.append(f"Negative corner speed V_G ({vg:.1f} m/s) exceeds V_C")

    # --- gust parameters ----------------------------------------------------
    mu_g = gust_mass_ratio(ws, rho, ac.wing_chord, ac.lift_curve_slope)
    k_g = gust_alleviation_factor(mu_g)
    ude = derived_gust_velocities(ac, altitude_m)

    def slope(u):
        return float(gust_increment(1.0, u, ws, ac.lift_curve_slope, k_g))

    speeds = {"vs1": vs1, "vs_neg": vs_neg, "va": va, "vg": vg, "vc": vc, "vd": vd}
    gust_lines: list[GustLine] = []
    vb = ac.v_b
    if vb is None and reg is Regulation.FAR25:
        # V_B where the V_B gust line meets the positive stall curve (25.335(d)), capped at V_C
        a_ = RHO0 * S * ac.cl_max_clean / (2.0 * W)
        kb = slope(ude["vb"])
        vb = min((kb + math.sqrt(kb * kb + 4.0 * a_)) / (2.0 * a_), vc)
    if vb is not None:
        speeds["vb"] = vb
        gust_lines.append(GustLine("vb", ude["vb"], slope(ude["vb"]), vb))
    gust_lines.append(GustLine("vc", ude["vc"], slope(ude["vc"]), vc))
    gust_lines.append(GustLine("vd", ude["vd"], slope(ude["vd"]), vd))

    # --- dense speed grid with exact corners --------------------------------
    corners = [s for s in speeds.values() if 0.0 < s <= vd]
    v = np.union1d(np.linspace(0.0, vd, n_points), np.asarray(corners))

    stall_pos = stall_load_factor(v, ac, ac.cl_max_clean)
    stall_neg = stall_load_factor(v, ac, ac.cl_min_clean)

    neg_profile = np.where(v <= vc, n_neg, n_neg + (n_neg_vd - n_neg) * (v - vc) / (vd - vc))
    maneuver_upper = np.minimum(stall_pos, n_pos)
    maneuver_lower = np.maximum(stall_neg, neg_profile)

    gx = np.array([0.0] + [g.design_speed for g in gust_lines])
    gy = np.array([0.0] + [g.delta_n for g in gust_lines])
    dn = np.interp(v, gx, gy)
    gust_upper = 1.0 + dn
    gust_lower = 1.0 - dn

    combined_upper = np.minimum(stall_pos, np.maximum(n_pos, gust_upper))
    combined_lower = np.maximum(stall_neg, np.minimum(neg_profile, gust_lower))

    n_design_pos = float(combined_upper.max())
    n_design_neg = float(combined_lower.min())
    if n_design_pos > n_pos + 1e-9:
        warnings.append(f"Gust loads govern: positive design load factor {n_design_pos:.2f} g "
                        f"exceeds maneuver limit {n_pos:.2f} g")
    if n_design_neg < n_neg - 1e-9:
        warnings.append(f"Gust loads govern: negative design load factor {n_design_neg:.2f} g "
                        f"exceeds maneuver limit {n_neg:.2f} g")

    return VnResult(
        aircraft=ac, altitude_m=float(altitude_m), rho=float(rho), sigma=float(sigma),
        regulation=reg, n_pos=n_pos, n_neg=n_neg, n_neg_vd=n_neg_vd, limit_sources=(src_p, src_n),
        n_design_pos=n_design_pos, n_design_neg=n_design_neg,
        n_ult_pos=ac.ultimate_factor * n_design_pos, n_ult_neg=ac.ultimate_factor * n_design_neg,
        speeds=speeds, mu_g=mu_g, k_g=k_g, gust_lines=gust_lines, v=v,
        stall_pos=stall_pos, stall_neg=stall_neg,
        maneuver_upper=maneuver_upper, maneuver_lower=maneuver_lower,
        gust_upper=gust_upper, gust_lower=gust_lower,
        combined_upper=combined_upper, combined_lower=combined_lower,
        warnings=warnings,
    )
