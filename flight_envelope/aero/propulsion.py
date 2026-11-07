"""Available-thrust models (vectorised).

* Jet:        T(V, h) = T_SL * sigma^m                      (m = thrust_lapse_exponent)
* Propeller:  T(V, h) = eta_p * P(h) / V,  P(h) = P_SL (1.132 sigma - 0.132)  (Gagg-Ferrar)
              optionally capped by the static thrust T_SL * sigma.

Compressibility / Mach effects on thrust are not modelled.
"""

from __future__ import annotations

import numpy as np

from ..models.aircraft import Aircraft
from .atmosphere import density_ratio


def power_lapse(sigma):
    """Gagg-Ferrar piston-engine power lapse P/P_SL."""
    return np.clip(1.132 * np.asarray(sigma) - 0.132, 0.0, None)


def thrust_available(aircraft: Aircraft, v_tas, altitude_m: float):
    """Maximum available thrust [N] at true airspeed ``v_tas`` [m/s]."""
    v = np.maximum(np.asarray(v_tas, dtype=float), 1.0)
    sigma = float(density_ratio(altitude_m))
    if aircraft.is_propeller:
        power = aircraft.max_power_sl * power_lapse(sigma)
        thrust = aircraft.prop_efficiency * power / v
        if aircraft.max_thrust_sl is not None:
            thrust = np.minimum(thrust, aircraft.max_thrust_sl * sigma)
        return thrust
    return np.full_like(v, aircraft.max_thrust_sl * sigma**aircraft.thrust_lapse_exponent)
