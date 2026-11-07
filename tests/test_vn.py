"""V-n diagram: textbook / 14 CFR 23 & 25 checks."""

from __future__ import annotations

import math

import numpy as np
import pytest

from flight_envelope.aero.atmosphere import RHO0, isa
from flight_envelope.aero.vn import (
    compute_vn, derived_gust_velocities, far_negative_limit, far_positive_limit,
    gust_alleviation_factor, gust_increment, gust_mass_ratio, resolve_limits, stall_load_factor,
)
from flight_envelope.models.aircraft import Category, Regulation
from flight_envelope.units import FT, KT, LBF

SLUG_FT3 = 515.378818  # kg/m^3 per slug/ft^3
G_FT = 32.174049


# ---------------------------------------------------------------- speeds
def test_vs1_and_va_textbook(any_aircraft):
    ac = any_aircraft
    r = compute_vn(ac)
    vs1 = math.sqrt(2 * ac.weight / (1.225 * ac.wing_area * ac.cl_max_clean))
    assert r.speeds["vs1"] == pytest.approx(vs1, rel=1e-4)
    assert r.speeds["va"] == pytest.approx(r.speeds["vs1"] * math.sqrt(r.n_pos), rel=1e-12)
    # Va lies on the positive stall curve at n_pos
    assert float(stall_load_factor(r.speeds["va"], ac, ac.cl_max_clean)) == pytest.approx(r.n_pos)
    vs_neg = math.sqrt(2 * ac.weight / (RHO0 * ac.wing_area * abs(ac.cl_min_clean)))
    assert r.speeds["vs_neg"] == pytest.approx(vs_neg)


def test_cessna_vs1_known_value(cessna):
    # ~52.6 KEAS clean stall at 2450 lb with CLmax 1.5, S = 16.2 m^2
    r = compute_vn(cessna)
    assert r.speeds["vs1"] / KT == pytest.approx(52.59, abs=0.05)


def test_maneuver_envelope_is_altitude_independent_in_eas(cessna):
    r0, r1 = compute_vn(cessna, 0.0), compute_vn(cessna, 3000.0)
    for k in ("vs1", "va", "vc", "vd"):
        assert r0.speeds[k] == pytest.approx(r1.speeds[k])


# ---------------------------------------------------------------- limits
def test_far23_337_formula():
    assert far_positive_limit(12_500.0, Category.NORMAL) == pytest.approx(2.1 + 24000 / 22500)
    assert far_positive_limit(12_500.0, Category.NORMAL) == pytest.approx(3.1667, abs=1e-4)


def test_far23_337_light_weight_capped():
    assert far_positive_limit(2_450.0, Category.NORMAL) == 3.8
    assert far_positive_limit(500.0, Category.COMMUTER) == 3.8


def test_far23_337_heavy_floored():
    assert far_positive_limit(150_000.0, Category.NORMAL) == 2.5
    assert far_positive_limit(150_000.0, Category.TRANSPORT) == 2.5


def test_category_limits():
    assert far_positive_limit(2_000.0, Category.UTILITY) == 4.4
    assert far_positive_limit(2_000.0, Category.AEROBATIC) == 6.0


@pytest.mark.parametrize("n", [2.5, 3.1667, 3.8, 4.4])
def test_negative_limit_is_0p4_n(n):
    assert far_negative_limit(n, Category.NORMAL) == pytest.approx(-0.4 * n)
    assert far_negative_limit(n, Category.UTILITY) == pytest.approx(-0.4 * n)


def test_transport_negative_limit():
    assert far_negative_limit(2.5, Category.TRANSPORT) == -1.0


def test_resolve_limits_user_override_and_derived(cessna, turboprop):
    n_pos, n_neg, sp, sn = resolve_limits(cessna)
    assert (n_pos, n_neg, sp, sn) == (3.8, -1.52, "user", "user")
    n_pos, n_neg, sp, sn = resolve_limits(turboprop)
    w_lbf = turboprop.weight / LBF
    assert n_pos == pytest.approx(min(max(2.1 + 24000 / (w_lbf + 10000), 2.5), 3.8))
    assert n_neg == -1.0
    assert "25.337" in sp


# ---------------------------------------------------------------- gust model
@pytest.mark.parametrize("mu", [1.0, 5.3, 20.0, 100.0])
def test_kg_formula(mu):
    assert gust_alleviation_factor(mu) == pytest.approx(0.88 * mu / (5.3 + mu))


def test_kg_bounds():
    assert gust_alleviation_factor(5.3) == pytest.approx(0.44)
    assert gust_alleviation_factor(1e9) == pytest.approx(0.88, rel=1e-6)


def _imperial_delta_n(ac, v_eas_ms, ude_ms, altitude_m=0.0):
    """Independent FAR 23.341 imperial gust formula."""
    ws_psf = ac.wing_loading / (LBF / FT**2)
    rho_slug = isa(altitude_m).density / SLUG_FT3
    chord_ft = ac.wing_chord / FT
    a = ac.lift_curve_slope
    mu = 2 * ws_psf / (rho_slug * chord_ft * a * G_FT)
    kg = 0.88 * mu / (5.3 + mu)
    v_keas = v_eas_ms / KT
    ude_fps = ude_ms / FT
    return kg * ude_fps * v_keas * a / (498.0 * ws_psf), mu


@pytest.mark.parametrize("altitude_m", [0.0, 3000.0])
def test_gust_increment_matches_imperial_far23(any_aircraft, altitude_m):
    ac = any_aircraft
    r = compute_vn(ac, altitude_m)
    for g in r.gust_lines:
        dn_imp, mu_imp = _imperial_delta_n(ac, g.design_speed, g.u_de, altitude_m)
        assert r.mu_g == pytest.approx(mu_imp, rel=1e-4)
        assert g.delta_n == pytest.approx(dn_imp, rel=5e-3)


def test_gust_increment_si_formula(cessna):
    ws, a = cessna.wing_loading, cessna.lift_curve_slope
    mu = gust_mass_ratio(ws, 1.225, cessna.wing_chord, a)
    assert mu == pytest.approx(2 * ws / (1.225 * cessna.wing_chord * 9.80665 * a))
    kg = gust_alleviation_factor(mu)
    dn = gust_increment(65.0, 50 * FT, ws, a, kg)
    assert float(dn) == pytest.approx(RHO0 * 65.0 * a * kg * 50 * FT / (2 * ws))
    # linear in V
    assert float(gust_increment(130.0, 50 * FT, ws, a, kg)) == pytest.approx(2 * float(dn))


@pytest.mark.parametrize(
    "h_ft, uc_fps, ub_fps, ud_fps",
    [(0, 50, 66, 25), (10_000, 50, 66, 25), (20_000, 50, 66, 25),
     (35_000, 37.5, 52, 18.75), (50_000, 25, 38, 12.5), (60_000, 25, 38, 12.5)],
)
def test_gust_velocity_altitude_reduction(cessna, h_ft, uc_fps, ub_fps, ud_fps):
    u = derived_gust_velocities(cessna, h_ft * FT)
    assert u["vc"] / FT == pytest.approx(uc_fps)
    assert u["vb"] / FT == pytest.approx(ub_fps)
    assert u["vd"] / FT == pytest.approx(ud_fps)


def test_gust_mass_ratio_increases_with_altitude(cessna):
    r0, r1 = compute_vn(cessna, 0.0), compute_vn(cessna, 3000.0)
    assert r1.mu_g > r0.mu_g
    assert r1.k_g > r0.k_g


# ---------------------------------------------------------------- FAR 25 V_B
def test_far25_vb_on_stall_curve(turboprop):
    r = compute_vn(turboprop)
    assert r.regulation is Regulation.FAR25
    assert "vb" in r.speeds
    vb = r.speeds["vb"]
    assert 0 < vb <= r.speeds["vc"]
    line = next(g for g in r.gust_lines if g.key == "vb")
    assert line.u_de / FT == pytest.approx(66.0)
    n_stall = float(stall_load_factor(vb, turboprop, turboprop.cl_max_clean))
    assert n_stall == pytest.approx(1.0 + line.delta_n, rel=1e-9)
    assert vb > r.speeds["vs1"]


def test_far23_has_no_vb_by_default(cessna):
    r = compute_vn(cessna)
    assert "vb" not in r.speeds
    assert [g.key for g in r.gust_lines] == ["vc", "vd"]


# ---------------------------------------------------------------- envelopes
def test_combined_contains_maneuver(any_aircraft):
    r = compute_vn(any_aircraft)
    assert np.all(r.combined_upper >= r.maneuver_upper - 1e-12)
    assert np.all(r.combined_lower <= r.maneuver_lower + 1e-12)


def test_combined_upper_bounded_by_stall(any_aircraft):
    r = compute_vn(any_aircraft)
    assert np.all(r.combined_upper <= r.stall_pos + 1e-12)
    assert np.all(r.combined_lower >= r.stall_neg - 1e-12)
    assert np.all(r.maneuver_upper <= r.n_pos + 1e-12)


def test_grid_contains_corners(any_aircraft):
    r = compute_vn(any_aircraft)
    for s in r.speeds.values():
        if s <= r.speeds["vd"]:
            assert np.any(np.isclose(r.v, s, rtol=0, atol=1e-12))
    assert r.v[0] == 0.0 and r.v[-1] == pytest.approx(r.speeds["vd"])


def test_ultimate_is_1p5_design(any_aircraft):
    r = compute_vn(any_aircraft)
    assert any_aircraft.ultimate_factor == 1.5
    assert r.n_ult_pos == pytest.approx(1.5 * r.n_design_pos)
    assert r.n_ult_neg == pytest.approx(1.5 * r.n_design_neg)
    assert r.n_design_pos == pytest.approx(r.combined_upper.max())
    assert r.n_design_pos >= r.n_pos - 1e-12


def test_cessna_is_gust_governed(cessna):
    r = compute_vn(cessna)
    assert r.gust_governs_positive
    assert r.n_design_pos > r.n_pos
    assert any("Gust loads govern" in w for w in r.warnings)


def test_f16_maneuver_governed(f16):
    r = compute_vn(f16)
    assert not r.gust_governs_positive
    assert r.n_design_pos == pytest.approx(9.0)
    assert not any("Gust loads govern" in w for w in r.warnings)


def test_negative_limit_ramps_to_zero_at_vd_far23(cessna):
    r = compute_vn(cessna)
    assert r.n_neg_vd == 0.0
    assert r.maneuver_lower[-1] == pytest.approx(0.0)


def test_with_overrides_mass_changes_vs1(cessna):
    heavy = cessna.with_overrides(mass=cessna.mass * 1.21)
    assert compute_vn(heavy).speeds["vs1"] == pytest.approx(compute_vn(cessna).speeds["vs1"] * 1.1)
