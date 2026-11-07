"""Unit conversion and quantity parsing.

All engines compute strictly in SI (kg, m, s, N, W, rad). This module converts
user-facing inputs (e.g. ``"2400 lb"``, ``"126 kt"``, ``"180 hp"``) into SI and
converts SI results into the display units requested for output.
"""

from __future__ import annotations

import math
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict

# ---------------------------------------------------------------------------
# Exact / standard constants
# ---------------------------------------------------------------------------
G0 = 9.80665  # standard gravity [m/s^2]
FT = 0.3048  # [m]
INCH = 0.0254  # [m]
NMI = 1852.0  # [m]
KT = NMI / 3600.0  # [m/s]
MPH = 1609.344 / 3600.0  # [m/s]
KMH = 1000.0 / 3600.0  # [m/s]
LBM = 0.45359237  # [kg]
LBF = LBM * G0  # [N]
SLUG = LBF / FT  # [kg]
HP = 550.0 * LBF * FT  # mechanical horsepower [W]
PS = 75.0 * G0  # metric horsepower [W]

_NUMBER_UNIT = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(.*?)\s*$")

_UNIT_TABLES: dict[str, dict[str, float]] = {
    "mass": {"kg": 1.0, "g": 1e-3, "t": 1000.0, "tonne": 1000.0, "lb": LBM, "lbs": LBM,
             "lbm": LBM, "slug": SLUG, "slugs": SLUG},
    "length": {"m": 1.0, "km": 1000.0, "cm": 0.01, "mm": 1e-3, "ft": FT, "feet": FT,
               "in": INCH, "inch": INCH, "nmi": NMI},
    "area": {"m2": 1.0, "ft2": FT**2, "sqft": FT**2, "sqm": 1.0, "in2": INCH**2},
    "speed": {"m/s": 1.0, "mps": 1.0, "kt": KT, "kts": KT, "kn": KT, "knot": KT, "knots": KT,
              "keas": KT, "kcas": KT, "km/h": KMH, "kmh": KMH, "kph": KMH, "mph": MPH,
              "ft/s": FT, "fps": FT},
    "force": {"n": 1.0, "kn": 1000.0, "lbf": LBF, "lb": LBF, "lbs": LBF, "klbf": 1000.0 * LBF,
              "kgf": G0},
    "power": {"w": 1.0, "kw": 1000.0, "mw": 1e6, "hp": HP, "bhp": HP, "shp": HP, "ps": PS},
    "lift_slope": {"/rad": 1.0, "1/rad": 1.0, "per_rad": 1.0, "rad^-1": 1.0,
                   "/deg": 180.0 / math.pi, "1/deg": 180.0 / math.pi,
                   "per_deg": 180.0 / math.pi, "deg^-1": 180.0 / math.pi},
}


def _normalise_unit(unit: str) -> str:
    u = unit.strip().lower().replace("²", "2").replace("^2", "2").replace(" ", "")
    return u.replace("per", "per_") if u.startswith("per") and "_" not in u else u


def parse_quantity(value: Any, kind: str) -> Any:
    """Parse ``value`` into SI for a physical ``kind``.

    Plain numbers are assumed to already be SI. Strings may carry a unit,
    e.g. ``"2450 lb"``, ``"174 ft^2"``, ``"126 kt"``, ``"0.1 /deg"``.
    Non-numeric/non-string values are passed through for pydantic to reject.
    """
    if value is None or isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return value
    m = _NUMBER_UNIT.match(value)
    if not m:
        raise ValueError(f"cannot parse quantity {value!r}")
    number, unit = float(m.group(1)), _normalise_unit(m.group(2))
    if not unit:
        return number
    table = _UNIT_TABLES[kind]
    if unit not in table:
        raise ValueError(f"unknown {kind} unit {m.group(2)!r}; expected one of {sorted(table)}")
    return number * table[unit]


# ---------------------------------------------------------------------------
# Output (display) units
# ---------------------------------------------------------------------------
class SpeedUnit(str, Enum):
    KT = "kt"
    MPS = "m/s"
    KMH = "km/h"
    MPH = "mph"


class SpeedType(str, Enum):
    EAS = "eas"
    TAS = "tas"


class LengthUnit(str, Enum):
    M = "m"
    FT = "ft"


SPEED_ALIASES = {"kt": SpeedUnit.KT, "kts": SpeedUnit.KT, "knot": SpeedUnit.KT,
                 "knots": SpeedUnit.KT, "kn": SpeedUnit.KT, "m/s": SpeedUnit.MPS,
                 "mps": SpeedUnit.MPS, "ms": SpeedUnit.MPS, "km/h": SpeedUnit.KMH,
                 "kmh": SpeedUnit.KMH, "kph": SpeedUnit.KMH, "mph": SpeedUnit.MPH}

_SPEED_FACTOR = {SpeedUnit.KT: KT, SpeedUnit.MPS: 1.0, SpeedUnit.KMH: KMH, SpeedUnit.MPH: MPH}
_LENGTH_FACTOR = {LengthUnit.M: 1.0, LengthUnit.FT: FT}


def resolve_speed_unit(name: str | SpeedUnit) -> SpeedUnit:
    if isinstance(name, SpeedUnit):
        return name
    try:
        return SPEED_ALIASES[name.strip().lower()]
    except KeyError as exc:
        raise ValueError(f"unknown speed unit {name!r}; use one of {sorted(SPEED_ALIASES)}") from exc


class OutputUnits(BaseModel):
    """Display units used when serialising an envelope."""

    model_config = ConfigDict(frozen=True)

    speed: SpeedUnit = SpeedUnit.KT
    speed_type: SpeedType = SpeedType.EAS
    length: LengthUnit = LengthUnit.M

    @property
    def speed_label(self) -> str:
        if self.speed is SpeedUnit.KT:
            return "KEAS" if self.speed_type is SpeedType.EAS else "KTAS"
        return f"{self.speed.value} {self.speed_type.value.upper()}"

    def speed_from_si(self, v):
        return v / _SPEED_FACTOR[self.speed]

    def speed_to_si(self, v):
        return v * _SPEED_FACTOR[self.speed]

    def length_from_si(self, x):
        return x / _LENGTH_FACTOR[self.length]

    def length_to_si(self, x):
        return x * _LENGTH_FACTOR[self.length]


def speed_from_si(v, unit: SpeedUnit):
    return v / _SPEED_FACTOR[unit]


def speed_to_si(v, unit: SpeedUnit):
    return v * _SPEED_FACTOR[unit]
