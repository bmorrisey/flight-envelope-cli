"""flight-envelope: aircraft V-n and turn-performance envelopes.

Public API::

    from flight_envelope import load_aircraft, vn_envelope, turn_envelope, OutputUnits
    ac = load_aircraft("examples/cessna172.yaml")
    result, data = vn_envelope(ac, altitude_m=0.0)
    data.to_json("vn.json")
"""

from .envelopes import build_turn_envelope, build_vn_envelope, turn_envelope, vn_envelope
from .models.aircraft import Aircraft, Category, Regulation, load_aircraft
from .models.outputs import CurveSeries, EnvelopeData, KeyPoint, Point2D, Region
from .units import LengthUnit, OutputUnits, SpeedType, SpeedUnit

__version__ = "0.1.0"

__all__ = [
    "Aircraft", "Category", "Regulation", "load_aircraft",
    "EnvelopeData", "CurveSeries", "KeyPoint", "Point2D", "Region",
    "OutputUnits", "SpeedUnit", "SpeedType", "LengthUnit",
    "vn_envelope", "turn_envelope", "build_vn_envelope", "build_turn_envelope",
]
