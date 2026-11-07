"""EnvelopeData contract: serialisation and content."""

from __future__ import annotations

import csv
import json

import pytest

from flight_envelope.envelopes import turn_envelope, vn_envelope
from flight_envelope.models.outputs import EnvelopeData
from flight_envelope.units import KT, OutputUnits, SpeedType, SpeedUnit


@pytest.fixture(scope="module")
def vn_env(cessna):
    return vn_envelope(cessna, n_points=200)


@pytest.fixture(scope="module")
def turn_env(f16):
    return turn_envelope(f16, 3000.0, n_points=200)


@pytest.fixture(params=["vn", "turn"])
def env(request, vn_env, turn_env):
    return (vn_env if request.param == "vn" else turn_env)[1]


def test_json_roundtrip_text(env):
    text = env.to_json()
    back = EnvelopeData.from_json(text)
    assert back == env
    assert json.loads(text)["schema_version"] == env.schema_version


def test_json_roundtrip_file(env, tmp_path):
    p = tmp_path / "env.json"
    env.export(p)
    assert EnvelopeData.from_json(p) == env
    assert EnvelopeData.from_json(str(p)) == env


def test_csv_export(env, tmp_path):
    p = tmp_path / "env.csv"
    env.export(p)
    with open(p, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["kind", "id", "label", "index", "x", "y", "x_unit", "y_unit"]
        rows = list(reader)
    boundary_ids = {r["id"] for r in rows if r["kind"] == "boundary"}
    for b in env.boundaries:
        if b.points:
            assert b.id in boundary_ids
            n = sum(1 for r in rows if r["kind"] == "boundary" and r["id"] == b.id)
            assert n == len(b.points)
    assert {r["id"] for r in rows if r["kind"] == "key_point"} == set(env.key_points)
    assert any(r["kind"] == "safe_region" for r in rows)


def test_export_rejects_txt(env, tmp_path):
    with pytest.raises(ValueError):
        env.export(tmp_path / "env.txt")
    assert not (tmp_path / "env.txt").exists()


def test_vn_key_points(vn_env):
    res, env = vn_env
    assert env.envelope_type == "vn_diagram"
    for k in ("Va", "Vs1", "Vc_pos", "Vd_pos", "Vc_neg", "Vd_neg"):
        assert k in env.key_points
    assert env.key_points["Vs1"].x == pytest.approx(res.speeds["vs1"] / KT, abs=1e-3)
    assert env.key_points["Vs1"].y == 1.0
    assert env.key_points["Va"].y == pytest.approx(res.n_pos)
    assert env.units["speed"] == "KEAS"


def test_vn_series_lookup(vn_env):
    _, env = vn_env
    assert env.series("combined_envelope").points
    assert env.series("gust_vc_pos").role == "gust"
    assert env.region("safe_envelope").points
    with pytest.raises(KeyError):
        env.series("nope")


def test_turn_key_points(turn_env):
    res, env = turn_env
    assert env.envelope_type == "turn_performance"
    assert "corner" in env.key_points
    assert env.key_points["corner"].x == pytest.approx(res.v_corner / KT, rel=1e-4)
    assert env.key_points["corner"].y == pytest.approx(res.omega_max_inst * 57.29577951, rel=1e-4)
    assert env.units["speed"] == "KTAS"


def test_safe_region_polygon_nonempty(env):
    assert len(env.safe_region_polygon) >= 4


def test_turn_safe_polygon_closed(turn_env):
    poly = turn_env[1].safe_region_polygon
    assert poly[0] == poly[-1]


def test_vn_tas_units(cessna):
    res, env = vn_envelope(cessna, 3000.0, OutputUnits(speed=SpeedUnit.MPS, speed_type=SpeedType.TAS),
                           n_points=100)
    assert env.units["speed"] == "m/s TAS"
    assert env.key_points["Vs1"].x == pytest.approx(res.speeds["vs1"] / res.sigma**0.5, abs=1e-3)
    assert any("TAS" in w for w in env.warnings)


def test_flight_conditions(vn_env, cessna):
    fc = vn_env[1].flight_conditions
    assert fc["mass_kg"] == pytest.approx(cessna.mass)
    assert fc["density_kg_m3"] == pytest.approx(1.225, abs=1e-4)
    assert fc["regulation"] == "far23"
