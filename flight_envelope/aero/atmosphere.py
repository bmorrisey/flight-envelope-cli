"""International Standard Atmosphere (ISA, ICAO Doc 7488 / US 1976), 0-86 km.

All functions are numpy-vectorised: they accept a float or an array of
geometric altitudes [m] and return the same shape (floats for scalar input).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..units import G0

R_AIR = 287.05287  # specific gas constant of dry air [J/(kg K)]
GAMMA = 1.4
R_EARTH = 6_356_766.0  # effective Earth radius for geopotential [m]

T0 = 288.15  # [K]
P0 = 101_325.0  # [Pa]
RHO0 = P0 / (R_AIR * T0)  # 1.225 kg/m^3
A0 = float(np.sqrt(GAMMA * R_AIR * T0))  # 340.294 m/s

# (geopotential base altitude [m], lapse rate [K/m])
_LAYERS = (
    (0.0, -0.0065),
    (11_000.0, 0.0),
    (20_000.0, 0.0010),
    (32_000.0, 0.0028),
    (47_000.0, 0.0),
    (51_000.0, -0.0028),
    (71_000.0, -0.0020),
)
H_MIN = -5_000.0
H_MAX = 84_852.0  # top of the 7th layer (geopotential)


def _layer_bases() -> list[tuple[float, float, float, float]]:
    bases = []
    t, p = T0, P0
    for i, (hb, lapse) in enumerate(_LAYERS):
        bases.append((hb, lapse, t, p))
        if i + 1 < len(_LAYERS):
            dh = _LAYERS[i + 1][0] - hb
            t_next = t + lapse * dh
            if lapse == 0.0:
                p = p * np.exp(-G0 * dh / (R_AIR * t))
            else:
                p = p * (t_next / t) ** (-G0 / (lapse * R_AIR))
            t = t_next
    return bases


_BASES = _layer_bases()


def geopotential_altitude(h):
    """Geometric altitude [m] -> geopotential altitude [m]."""
    h = np.asarray(h, dtype=float)
    return R_EARTH * h / (R_EARTH + h)


@dataclass(frozen=True)
class AtmosphereState:
    altitude: np.ndarray | float  # geometric [m]
    temperature: np.ndarray | float  # [K]
    pressure: np.ndarray | float  # [Pa]
    density: np.ndarray | float  # [kg/m^3]
    speed_of_sound: np.ndarray | float  # [m/s]

    @property
    def sigma(self):
        """Density ratio rho / rho0."""
        return self.density / RHO0


def isa(h) -> AtmosphereState:
    """Standard-day atmospheric properties at geometric altitude ``h`` [m]."""
    scalar = np.ndim(h) == 0
    hg = np.atleast_1d(np.asarray(h, dtype=float))
    H = geopotential_altitude(hg)
    if np.any(H < H_MIN) or np.any(H > H_MAX):
        raise ValueError(f"ISA model valid for geopotential altitude {H_MIN:.0f} .. {H_MAX:.0f} m")

    T = np.empty_like(H)
    p = np.empty_like(H)
    for i, (hb, lapse, tb, pb) in enumerate(_BASES):
        upper = _BASES[i + 1][0] if i + 1 < len(_BASES) else np.inf
        mask = (H >= hb) & (H < upper) if i > 0 else (H < upper)
        if not np.any(mask):
            continue
        dh = H[mask] - hb
        T[mask] = tb + lapse * dh
        if lapse == 0.0:
            p[mask] = pb * np.exp(-G0 * dh / (R_AIR * tb))
        else:
            p[mask] = pb * (T[mask] / tb) ** (-G0 / (lapse * R_AIR))

    rho = p / (R_AIR * T)
    a = np.sqrt(GAMMA * R_AIR * T)
    if scalar:
        return AtmosphereState(float(hg[0]), float(T[0]), float(p[0]), float(rho[0]), float(a[0]))
    return AtmosphereState(hg, T, p, rho, a)


def density(h):
    return isa(h).density


def density_ratio(h):
    return isa(h).sigma


def speed_of_sound(h):
    return isa(h).speed_of_sound


def dynamic_pressure(rho, v):
    """q = 1/2 rho V^2 [Pa]."""
    return 0.5 * np.asarray(rho) * np.asarray(v) ** 2


def eas_to_tas(v_eas, h):
    return np.asarray(v_eas) / np.sqrt(density_ratio(h))


def tas_to_eas(v_tas, h):
    return np.asarray(v_tas) * np.sqrt(density_ratio(h))
