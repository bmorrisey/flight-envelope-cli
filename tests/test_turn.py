"""Turn performance: kinematics and E-M boundaries."""

from __future__ import annotations

import math

import numpy as np
import pytest

from flight_envelope.aero.atmosphere import isa
from flight_envelope.aero.propulsion import power_lapse, thrust_available
from flight_envelope.aero.turn import (
    bank_from_load_factor, compute_turn, drag, load_factor_from_bank, turn_radius, turn_rate,
    turn_rate_from_bank,
)
from flight_envelope.units import FT


# ---------------------------------------------------------------- kinematics
def test_bank_60_gives_2g():
    assert float(load_factor_from_bank(math.radians(60.0))) == pytest.approx(2.0)
    assert math.degrees(float(bank_from_load_factor(2.0))) == pytest.approx(60.0)


def test_turn_rate_and_radius_reference():
    # Handbook values (0.16991 rad/s, 9.735 deg/s, 588.6 m) use g = 9.81; the package
    # uses standard gravity 9.80665, so compare exactly against g0 and loosely to the handbook.
    w = float(turn_rate(100.0, 2.0))
    assert w == pytest.approx(9.80665 * math.sqrt(3.0) / 100.0, rel=1e-12)
    assert w == pytest.approx(0.16991, rel=5e-4)
    assert math.degrees(w) == pytest.approx(9.735, rel=5e-4)
    R = float(turn_radius(100.0, 2.0))
    assert R == pytest.approx(100.0**2 / (9.80665 * math.sqrt(3.0)), rel=1e-12)
    assert R == pytest.approx(588.6, rel=5e-4)


@pytest.mark.parametrize("v, n", [(50.0, 1.2), (100.0, 2.0), (250.0, 9.0)])
def test_omega_times_radius_is_v(v, n):
    assert float(turn_rate(v, n)) * float(turn_radius(v, n)) == pytest.approx(v)


def test_turn_rate_from_bank_consistent():
    phi = math.radians(45.0)
    assert float(turn_rate_from_bank(120.0, phi)) == pytest.approx(
        float(turn_rate(120.0, load_factor_from_bank(phi))))


def test_level_flight_degenerate():
    assert float(turn_rate(100.0, 1.0)) == 0.0
    assert math.isinf(float(turn_radius(100.0, 1.0)))
    assert np.all(turn_rate(np.array([50.0, 80.0]), np.array([0.5, 1.0])) == 0.0)


# ---------------------------------------------------------------- corner / instantaneous
@pytest.mark.parametrize("altitude_m", [0.0, 6000.0])
def test_corner_speed_and_min_radius(any_aircraft, altitude_m):
    ac = any_aircraft
    r = compute_turn(ac, altitude_m)
    rho = isa(altitude_m).density
    v_star = math.sqrt(2 * r.n_limit * ac.weight / (rho * ac.wing_area * ac.cl_max_clean))
    assert r.v_corner == pytest.approx(v_star, rel=1e-12)
    if v_star < r.v_max:
        assert r.omega_max_inst == pytest.approx(float(turn_rate(v_star, r.n_limit)), rel=1e-9)
        assert r.radius_min == pytest.approx(float(turn_radius(v_star, r.n_limit)), rel=1e-9)
        ic = int(np.argmax(r.omega_inst))
        assert r.v[ic] == pytest.approx(v_star)
        assert int(np.argmin(r.radius_inst)) == ic


def test_inst_is_min_of_aero_and_structural(any_aircraft):
    r = compute_turn(any_aircraft)
    assert np.allclose(r.n_inst, np.minimum(r.n_aero, r.n_limit))
    assert np.all(r.omega_inst <= r.omega_struct + 1e-12)
    assert np.all(r.omega_inst <= r.omega_aero + 1e-12)


# ---------------------------------------------------------------- sustained
def test_sustained_below_instantaneous(any_aircraft):
    r = compute_turn(any_aircraft)
    ok = np.isfinite(r.n_sustained)
    assert ok.any()
    assert np.all(r.n_sustained[ok] <= r.n_inst[ok] + 1e-12)
    assert np.all(r.omega_sustained[ok] <= r.omega_inst[ok] + 1e-12)
    assert np.all(r.n_sustained[ok] >= 1.0)


def test_thrust_equals_drag_on_sustained_boundary(any_aircraft):
    ac = any_aircraft
    for h in (0.0, 5000.0):
        r = compute_turn(ac, h)
        T = thrust_available(ac, r.v, h)
        assert np.allclose(T, r.thrust)
        ok = np.isfinite(r.n_sustained) & (r.n_sustained < r.n_inst - 1e-6)
        if not ok.any():
            continue
        D = drag(r.v[ok], r.n_sustained[ok], r.rho, ac)
        assert np.allclose(D, T[ok], rtol=1e-9)


def test_max_sustained_point_is_consistent(f16):
    r = compute_turn(f16)
    assert r.omega_max_sus == pytest.approx(float(turn_rate(r.v_max_sus, r.n_max_sus)))
    assert r.omega_max_sus < r.omega_max_inst


def test_ps_contours_below_instantaneous(any_aircraft):
    r = compute_turn(any_aircraft, ps_levels=[-5.0, -20.0, 10.0])
    assert r.ps_contours
    for ps, om in r.ps_contours.items():
        ok = np.isfinite(om)
        assert np.all(om[ok] <= r.omega_inst[ok] + 1e-9)
        if ps < 0:  # bleeding energy: tighter than sustained where both exist
            both = ok & np.isfinite(r.omega_sustained)
            assert np.all(om[both] >= r.omega_sustained[both] - 1e-9)


def test_zero_ps_level_skipped(f16):
    r = compute_turn(f16, ps_levels=[0.0, -50.0])
    assert list(r.ps_contours) == [-50.0]


def test_auto_ps_levels_negative(f16):
    r = compute_turn(f16)
    assert r.ps_contours
    assert all(k < 0 for k in r.ps_contours)


def test_f16_higher_altitude_lower_sustained_rate(f16):
    rates = [compute_turn(f16, h * FT).omega_max_sus for h in (0, 15_000, 30_000)]
    assert rates[0] > rates[1] > rates[2] > 0


def test_corner_speed_tas_increases_with_altitude(f16):
    assert compute_turn(f16, 9000.0).v_corner > compute_turn(f16, 0.0).v_corner


# ---------------------------------------------------------------- propulsion
def test_jet_thrust_lapse(f16):
    sigma = isa(6000.0).sigma
    t = thrust_available(f16, np.array([100.0, 300.0]), 6000.0)
    assert np.allclose(t, f16.max_thrust_sl * sigma ** f16.thrust_lapse_exponent)


def test_prop_thrust_power_and_cap(cessna):
    v = np.array([5.0, 60.0])
    t = thrust_available(cessna, v, 0.0)
    assert t[0] == pytest.approx(cessna.max_thrust_sl)  # static cap
    assert t[1] == pytest.approx(cessna.prop_efficiency * cessna.max_power_sl / 60.0)
    assert float(power_lapse(1.0)) == pytest.approx(1.0)


def test_auto_ps_levels_helper():
    from flight_envelope.aero.turn import auto_ps_levels

    assert auto_ps_levels(-0.5) == []
    lv = auto_ps_levels(-120.0)
    assert 1 <= len(lv) <= 3
    assert all(-120.0 < x < 0 for x in lv)
    assert len(set(lv)) == len(lv)


def test_auto_ps_levels_round_in_display_unit(f16):
    r = compute_turn(f16, ps_unit=FT)
    assert r.ps_contours
    for ps in r.ps_contours:
        assert ps / FT == pytest.approx(round(ps / FT), abs=1e-9)
