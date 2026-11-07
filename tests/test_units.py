from __future__ import annotations

import math

import pytest

from flight_envelope.units import (
    FT, HP, KT, LBF, LBM, OutputUnits, SpeedType, SpeedUnit, parse_quantity, resolve_speed_unit,
)


@pytest.mark.parametrize(
    "text, kind, expected",
    [
        ("2450 lb", "mass", 2450 * 0.45359237),
        ("174 ft^2", "area", 174 * 0.3048**2),
        ("174 ft²", "area", 174 * 0.3048**2),
        ("126 kt", "speed", 126 * 1852.0 / 3600.0),
        ("180 hp", "power", 180 * 745.69987),
        ("0.1 /deg", "lift_slope", 0.1 * 180.0 / math.pi),
        ("5.7 /rad", "lift_slope", 5.7),
        ("29000 lbf", "force", 29000 * 4.4482216),
        ("60 kN", "force", 60000.0),
        ("1.5e3 kg", "mass", 1500.0),
        ("11.32 ft", "length", 11.32 * 0.3048),
    ],
)
def test_parse_quantity(text, kind, expected):
    assert parse_quantity(text, kind) == pytest.approx(expected, rel=1e-6)


def test_constants():
    assert KT == pytest.approx(0.514444, rel=1e-6)
    assert LBF == pytest.approx(4.4482216, rel=1e-7)
    assert HP == pytest.approx(745.69987, rel=1e-7)
    assert FT == 0.3048 and LBM == 0.45359237


@pytest.mark.parametrize("value", [1111.0, 7, None])
def test_plain_numbers_pass_through(value):
    assert parse_quantity(value, "mass") is value or parse_quantity(value, "mass") == value


def test_unitless_string_is_number():
    assert parse_quantity("42.5", "mass") == 42.5


@pytest.mark.parametrize("text, kind", [("10 furlongs", "length"), ("3 hp", "mass"), ("abc", "mass")])
def test_unknown_unit_raises(text, kind):
    with pytest.raises(ValueError):
        parse_quantity(text, kind)


def test_output_unit_labels():
    assert OutputUnits().speed_label == "KEAS"
    assert OutputUnits(speed_type=SpeedType.TAS).speed_label == "KTAS"
    assert OutputUnits(speed=SpeedUnit.MPS, speed_type=SpeedType.EAS).speed_label == "m/s EAS"
    assert OutputUnits(speed=SpeedUnit.KMH, speed_type=SpeedType.TAS).speed_label == "km/h TAS"


def test_output_unit_conversion_roundtrip():
    u = OutputUnits()
    assert u.speed_from_si(KT) == pytest.approx(1.0)
    assert u.speed_to_si(u.speed_from_si(77.7)) == pytest.approx(77.7)


def test_resolve_speed_unit():
    assert resolve_speed_unit("Knots") is SpeedUnit.KT
    assert resolve_speed_unit("km/h") is SpeedUnit.KMH
    with pytest.raises(ValueError):
        resolve_speed_unit("furlongs/fortnight")
