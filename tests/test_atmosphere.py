"""ISA checks against the US Standard Atmosphere 1976 tables."""

from __future__ import annotations

import numpy as np
import pytest

from flight_envelope.aero import atmosphere as atm
from flight_envelope.aero.atmosphere import R_EARTH, eas_to_tas, isa, tas_to_eas


def geometric(H: float) -> float:
    """Geopotential -> geometric altitude (inverse of atm.geopotential_altitude)."""
    return R_EARTH * H / (R_EARTH - H)


def test_geopotential_inversion_roundtrip():
    for H in (0.0, 5000.0, 11000.0, 20000.0, 47000.0):
        assert atm.geopotential_altitude(geometric(H)) == pytest.approx(H, abs=1e-6)


def test_sea_level():
    s = isa(0.0)
    assert s.temperature == pytest.approx(288.15, abs=1e-9)
    assert s.pressure == pytest.approx(101325.0, abs=1e-6)
    assert s.density == pytest.approx(1.225, abs=5e-5)
    assert s.speed_of_sound == pytest.approx(340.29, abs=0.01)
    assert s.sigma == pytest.approx(1.0)


@pytest.mark.parametrize(
    "H, T, p, rho",
    [
        (5000.0, 255.65, 54019.9, 0.73612),
        (11000.0, 216.65, 22632.1, 0.36392),
        (20000.0, 216.65, 5474.89, 0.088035),
    ],
)
def test_tabulated_geopotential(H, T, p, rho):
    s = isa(geometric(H))
    assert s.temperature == pytest.approx(T, abs=1e-3)
    assert s.pressure == pytest.approx(p, rel=2e-5)
    assert s.density == pytest.approx(rho, rel=2e-4)


def test_tabulated_geometric_5000m():
    # US Std Atm 1976 geometric-altitude table, Z = 5000 m (H = 4996.07 m)
    s = isa(5000.0)
    assert s.temperature == pytest.approx(255.676, abs=1e-3)
    assert s.pressure == pytest.approx(54048.3, rel=2e-5)
    assert s.density == pytest.approx(0.73643, rel=1e-4)


def test_geometric_input_differs_from_geopotential():
    # Passing 11 km geometric is *not* the tropopause: it is ~19 m geopotential lower.
    assert isa(11000.0).temperature > 216.65
    assert isa(geometric(11000.0) + 1.0).temperature == pytest.approx(216.65, abs=1e-9)


def test_vectorised_array_in_array_out():
    h = np.array([0.0, 1000.0, 5000.0, 15000.0])
    s = isa(h)
    for arr in (s.temperature, s.pressure, s.density, s.speed_of_sound):
        assert isinstance(arr, np.ndarray)
        assert arr.shape == h.shape
    for i, hi in enumerate(h):
        assert s.density[i] == pytest.approx(isa(float(hi)).density)
    assert np.all(np.diff(s.density) < 0)


def test_scalar_in_float_out():
    s = isa(3000.0)
    assert isinstance(s.temperature, float)
    assert isinstance(s.density, float)
    assert isinstance(isa(np.float64(100.0)).pressure, float)


@pytest.mark.parametrize("h", [-6000.0, 90_000.0])
def test_out_of_range_raises(h):
    with pytest.raises(ValueError):
        isa(h)
    with pytest.raises(ValueError):
        isa(np.array([0.0, h]))


def test_eas_tas_roundtrip():
    v = np.array([50.0, 100.0, 250.0])
    for h in (0.0, 3000.0, 10000.0):
        assert np.allclose(tas_to_eas(eas_to_tas(v, h), h), v)
    assert np.allclose(eas_to_tas(v, 0.0), v)
    # TAS > EAS above sea level
    assert np.all(eas_to_tas(v, 6000.0) > v)
    sigma = isa(6000.0).sigma
    assert eas_to_tas(100.0, 6000.0) == pytest.approx(100.0 / np.sqrt(sigma))


def test_dynamic_pressure():
    assert atm.dynamic_pressure(1.225, 100.0) == pytest.approx(6125.0)
