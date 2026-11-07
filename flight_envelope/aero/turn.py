r"""Turn-performance (doghouse / Energy-Maneuverability) engine.

Coordinated, level turn kinematics (true airspeed V):

.. math::
    n = 1/\cos\phi, \qquad
    \omega = \frac{g\sqrt{n^2-1}}{V}, \qquad
    R = \frac{V^2}{g\sqrt{n^2-1}}

Boundaries
* aerodynamic (instantaneous): :math:`n = q C_{L,max} / (W/S)`
* structural: :math:`n = n_{limit}`
* dynamic-pressure limit: :math:`V_D` (EAS) converted to TAS at altitude
* sustained (:math:`P_s = 0`): :math:`T = q S C_{D0} + K n^2 W^2 / (q S)`
* specific excess power contours :math:`P_s = V (T - D) / W`

Drag polar is parabolic and incompressible; thrust is from :mod:`.propulsion`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ..models.aircraft import Aircraft
from ..units import G0
from .atmosphere import isa
from .propulsion import thrust_available
from .vn import resolve_limits

DEFAULT_BANK_ANGLES = (30.0, 45.0, 60.0, 70.0, 80.0)


# ---------------------------------------------------------------------------
# Kinematics (pure functions, vectorised)
# ---------------------------------------------------------------------------
def load_factor_from_bank(phi_rad):
    return 1.0 / np.cos(phi_rad)


def bank_from_load_factor(n):
    return np.arccos(1.0 / np.asarray(n, dtype=float))


def turn_rate(v_tas, n):
    """Level-turn rate [rad/s]; zero where n <= 1."""
    v = np.asarray(v_tas, dtype=float)
    n = np.asarray(n, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 1.0, G0 * np.sqrt(np.maximum(n * n - 1.0, 0.0)) / v, 0.0)


def turn_radius(v_tas, n):
    """Level-turn radius [m]; +inf where n <= 1."""
    v = np.asarray(v_tas, dtype=float)
    n = np.asarray(n, dtype=float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 1.0, v * v / (G0 * np.sqrt(np.maximum(n * n - 1.0, 1e-300))), np.inf)


def turn_rate_from_bank(v_tas, phi_rad):
    return G0 * np.tan(phi_rad) / np.asarray(v_tas, dtype=float)


def aero_load_factor(v_tas, rho: float, ac: Aircraft):
    """n_max,aero = q C_Lmax / (W/S)."""
    return 0.5 * rho * np.asarray(v_tas) ** 2 * ac.cl_max_clean / ac.wing_loading


def drag(v_tas, n, rho: float, ac: Aircraft):
    """D = q S C_D0 + K n^2 W^2 / (q S)."""
    q = 0.5 * rho * np.asarray(v_tas) ** 2
    return q * ac.wing_area * ac.cd0 + ac.induced_drag_factor * (n * ac.weight) ** 2 / (q * ac.wing_area)


def load_factor_for_ps(v_tas, ps: float, thrust, rho: float, ac: Aircraft):
    """Load factor at which specific excess power equals ``ps`` [m/s] (NaN if unattainable)."""
    v = np.asarray(v_tas, dtype=float)
    q = 0.5 * rho * v**2
    qs = q * ac.wing_area
    available = thrust - ps * ac.weight / v - qs * ac.cd0
    with np.errstate(invalid="ignore"):
        n2 = available * qs / (ac.induced_drag_factor * ac.weight**2)
        return np.where(n2 >= 0.0, np.sqrt(np.maximum(n2, 0.0)), np.nan)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnResult:
    """Raw SI output of the turn engine (speeds are TAS m/s, rates rad/s)."""

    aircraft: Aircraft
    altitude_m: float
    rho: float
    sigma: float
    speed_of_sound: float
    n_limit: float

    v_stall_1g: float
    v_corner: float
    v_max: float  # q-limit (V_D EAS -> TAS)
    omega_max_inst: float
    radius_min: float
    omega_max_sus: float
    v_max_sus: float
    n_max_sus: float
    v_level_range: tuple[float, float] | None  # T = D at n = 1 (within grid)

    v: np.ndarray
    thrust: np.ndarray
    n_aero: np.ndarray
    n_inst: np.ndarray
    n_sustained: np.ndarray  # min(raw, n_inst); NaN where < 1
    omega_aero: np.ndarray
    omega_struct: np.ndarray
    omega_inst: np.ndarray
    omega_sustained: np.ndarray  # NaN where unsustainable
    radius_inst: np.ndarray
    ps_contours: dict[float, np.ndarray]  # Ps [m/s] -> omega (NaN outside envelope)
    warnings: list[str] = field(default_factory=list)


def auto_ps_levels(ps_floor: float) -> list[float]:
    """Up to three 'nice' negative P_s levels at ~25/50/75 % of the reachable bleed rate."""
    if ps_floor >= -1.0:
        return []
    nice = np.array([-2, -5, -10, -15, -20, -30, -50, -75, -100, -150, -200, -300, -400, -600], float)
    levels = []
    for frac in (0.25, 0.5, 0.75):
        c = float(nice[np.argmin(np.abs(nice - frac * ps_floor))])
        if c > ps_floor and c not in levels:
            levels.append(c)
    return levels


def compute_turn(
    ac: Aircraft,
    altitude_m: float = 0.0,
    ps_levels: list[float] | None = None,
    n_points: int = 600,
    ps_unit: float = 1.0,
) -> TurnResult:
    """Compute instantaneous, structural, sustained and P_s turn boundaries.

    ``ps_levels`` are in m/s; when omitted, round levels are chosen in units of
    ``ps_unit`` metres per second.
    """
    atm = isa(altitude_m)
    rho, sigma, a = atm.density, atm.sigma, atm.speed_of_sound
    n_lim, _, _, _ = resolve_limits(ac)
    W, S = ac.weight, ac.wing_area
    warnings: list[str] = []

    v_s1g = math.sqrt(2.0 * W / (rho * S * ac.cl_max_clean))
    v_corner = math.sqrt(2.0 * n_lim * W / (rho * S * ac.cl_max_clean))
    v_max = ac.v_d_resolved / math.sqrt(sigma)
    if v_corner > v_max:
        warnings.append("Corner speed exceeds V_D at this altitude; envelope is "
                        "aerodynamically limited throughout")

    v = np.union1d(np.linspace(v_s1g, v_max, n_points), [min(v_corner, v_max)])
    thrust = thrust_available(ac, v, altitude_m)

    n_aero = aero_load_factor(v, rho, ac)
    n_inst = np.minimum(n_aero, n_lim)
    omega_aero = turn_rate(v, n_aero)
    omega_struct = turn_rate(v, np.full_like(v, n_lim))
    omega_inst = turn_rate(v, n_inst)
    radius_inst = turn_radius(v, n_inst)

    n_s_raw = load_factor_for_ps(v, 0.0, thrust, rho, ac)
    n_sus = np.minimum(n_s_raw, n_inst)
    n_sus = np.where(n_sus >= 1.0, n_sus, np.nan)
    omega_sus = np.where(np.isnan(n_sus), np.nan, turn_rate(v, np.nan_to_num(n_sus)))

    level = n_s_raw >= 1.0
    v_level = (float(v[level].min()), float(v[level].max())) if np.any(level) else None
    if not np.any(np.isfinite(omega_sus)):
        warnings.append("No sustained level turn is possible at this altitude (T < D at n = 1)")
        omega_max_sus = v_max_sus = n_max_sus = 0.0
    else:
        i = int(np.nanargmax(omega_sus))
        omega_max_sus, v_max_sus, n_max_sus = float(omega_sus[i]), float(v[i]), float(n_sus[i])

    ic = int(np.argmax(omega_inst))
    omega_max_inst = float(omega_inst[ic])
    finite_r = radius_inst[np.isfinite(radius_inst)]
    radius_min = float(finite_r.min()) if finite_r.size else math.inf

    # P_s contours: P_s at the corner bounds the reachable levels
    if ps_levels is None:
        ps_corner = float(v[ic] * (thrust[ic] - drag(v[ic], n_inst[ic], rho, ac)) / W)
        # choose round numbers in the display unit (ps_unit metres, e.g. 0.3048 for ft/s)
        ps_levels = [lv * ps_unit for lv in auto_ps_levels(min(ps_corner, 0.0) / ps_unit)]
    ps_contours: dict[float, np.ndarray] = {}
    for ps in ps_levels:
        if ps == 0.0:
            continue  # identical to the sustained boundary
        n_ps = load_factor_for_ps(v, ps, thrust, rho, ac)
        valid = (n_ps >= 1.0) & (n_ps <= n_inst + 1e-9)
        ps_contours[float(ps)] = np.where(valid, turn_rate(v, np.nan_to_num(n_ps)), np.nan)

    return TurnResult(
        aircraft=ac, altitude_m=float(altitude_m), rho=float(rho), sigma=float(sigma),
        speed_of_sound=float(a), n_limit=n_lim, v_stall_1g=v_s1g, v_corner=v_corner, v_max=v_max,
        omega_max_inst=omega_max_inst, radius_min=radius_min, omega_max_sus=omega_max_sus,
        v_max_sus=v_max_sus, n_max_sus=n_max_sus, v_level_range=v_level, v=v, thrust=thrust,
        n_aero=n_aero, n_inst=n_inst, n_sustained=n_sus, omega_aero=omega_aero,
        omega_struct=omega_struct, omega_inst=omega_inst, omega_sustained=omega_sus,
        radius_inst=radius_inst, ps_contours=ps_contours, warnings=warnings,
    )
