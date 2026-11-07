"""Aircraft configuration schema (Pydantic v2).

Numeric fields accept plain SI numbers or strings with units, e.g.::

    mass: "2450 lb"
    wing_area: "174 ft^2"
    v_c: "126 kt"          # design speeds are equivalent airspeeds (EAS)
    lift_curve_slope: "0.084 /deg"
    max_power_sl: "180 hp"
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, Optional

import yaml
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from ..units import G0, parse_quantity


def _q(kind: str):
    return BeforeValidator(lambda v: parse_quantity(v, kind))


Mass = Annotated[float, _q("mass"), Field(gt=0, description="kg")]
Length = Annotated[float, _q("length"), Field(gt=0, description="m")]
Area = Annotated[float, _q("area"), Field(gt=0, description="m^2")]
Speed = Annotated[float, _q("speed"), Field(gt=0, description="m/s EAS")]
Force = Annotated[float, _q("force"), Field(gt=0, description="N")]
Power = Annotated[float, _q("power"), Field(gt=0, description="W")]
LiftSlope = Annotated[float, _q("lift_slope"), Field(gt=0, description="1/rad")]


class Category(str, Enum):
    NORMAL = "normal"
    UTILITY = "utility"
    AEROBATIC = "aerobatic"
    COMMUTER = "commuter"
    TRANSPORT = "transport"
    MILITARY = "military"


class Regulation(str, Enum):
    FAR23 = "far23"
    FAR25 = "far25"
    MILITARY = "military"


class GustVelocities(BaseModel):
    """Sea-level derived gust velocities U_de (EAS) at V_B, V_C, V_D.

    Defaults follow FAR 23.341 / 25.341 (pre-Amdt. 25-86 discrete gust):
    66 / 50 / 25 ft/s at or below 20 000 ft, reducing linearly to
    38 / 25 / 12.5 ft/s at 50 000 ft.
    """

    model_config = ConfigDict(extra="forbid")

    u_b: Annotated[float, _q("speed")] = 66.0 * 0.3048
    u_c: Annotated[float, _q("speed")] = 50.0 * 0.3048
    u_d: Annotated[float, _q("speed")] = 25.0 * 0.3048
    u_b_50k: Annotated[float, _q("speed")] = 38.0 * 0.3048
    u_c_50k: Annotated[float, _q("speed")] = 25.0 * 0.3048
    u_d_50k: Annotated[float, _q("speed")] = 12.5 * 0.3048


class Aircraft(BaseModel):
    """Geometric, aerodynamic, propulsive and structural definition of an aircraft."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    name: str
    category: Category = Category.NORMAL
    regulation: Optional[Regulation] = Field(
        None, description="Certification basis; derived from category when omitted")

    # Mass & geometry
    mass: Mass
    wing_area: Area
    wing_chord: Length = Field(
        description="Mean geometric chord S/b [m], used in the gust mass ratio (14 CFR 23.341); "
                    "the MAC is a close substitute for moderately tapered wings")
    aspect_ratio: float = Field(gt=0)
    oswald_efficiency: float = Field(gt=0, le=1.2)

    # Aerodynamics
    cd0: float = Field(gt=0, lt=1)
    cl_max_clean: float = Field(gt=0)
    cl_min_clean: float = Field(lt=0)
    lift_curve_slope: LiftSlope = Field(description="dCL/dalpha of the whole aircraft [1/rad]")

    # Propulsion: jet (thrust only) or propeller (power, optionally capped by static thrust)
    max_thrust_sl: Optional[Force] = Field(None, description="Max (static) thrust at SL [N]")
    max_power_sl: Optional[Power] = Field(None, description="Max shaft power at SL [W]")
    prop_efficiency: float = Field(0.80, gt=0, le=1)
    thrust_lapse_exponent: float = Field(
        0.7, ge=0, description="Jet thrust lapse T = T_SL * sigma^m")

    # Design speeds (EAS)
    v_c: Speed = Field(description="Design cruise speed V_C [m/s EAS]")
    v_d: Optional[Speed] = Field(None, description="Design dive speed V_D [m/s EAS]; default 1.25 V_C")
    v_b: Optional[Speed] = Field(None, description="Design speed for max gust intensity V_B [m/s EAS]")

    # Structural limits
    n_pos_limit: Optional[float] = Field(None, gt=0)
    n_neg_limit: Optional[float] = Field(None, lt=0)
    n_neg_at_vd: Optional[float] = Field(
        None, le=0, description="Negative limit at V_D (default per regulation)")
    ultimate_factor: float = Field(1.5, ge=1.0)
    gust: GustVelocities = Field(default_factory=GustVelocities)

    @model_validator(mode="after")
    def _check(self) -> "Aircraft":
        if self.max_thrust_sl is None and self.max_power_sl is None:
            raise ValueError("specify max_thrust_sl (jet) and/or max_power_sl (propeller)")
        if self.v_d is not None and self.v_d <= self.v_c:
            raise ValueError("v_d must exceed v_c")
        if self.v_b is not None and self.v_b > self.v_c:
            raise ValueError("v_b may not exceed v_c")
        return self

    # ------------------------------------------------------------------ derived
    @property
    def weight(self) -> float:
        """W = m g [N]."""
        return self.mass * G0

    @property
    def wing_loading(self) -> float:
        """W/S [Pa]."""
        return self.weight / self.wing_area

    @property
    def induced_drag_factor(self) -> float:
        """K = 1 / (pi AR e)."""
        import math

        return 1.0 / (math.pi * self.aspect_ratio * self.oswald_efficiency)

    @property
    def is_propeller(self) -> bool:
        return self.max_power_sl is not None

    @property
    def v_d_resolved(self) -> float:
        return self.v_d if self.v_d is not None else 1.25 * self.v_c

    @property
    def regulation_resolved(self) -> Regulation:
        if self.regulation is not None:
            return self.regulation
        if self.category is Category.TRANSPORT:
            return Regulation.FAR25
        if self.category is Category.MILITARY:
            return Regulation.MILITARY
        return Regulation.FAR23

    def with_overrides(self, **updates: Any) -> "Aircraft":
        """Return a re-validated copy with some fields replaced (None values ignored)."""
        data = self.model_dump()
        data.update({k: v for k, v in updates.items() if v is not None})
        return Aircraft.model_validate(data)


def load_aircraft(path: str | Path) -> Aircraft:
    """Load an aircraft from YAML or JSON (optionally wrapped in an ``aircraft:`` key)."""
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    data = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping at top level")
    if "aircraft" in data and isinstance(data["aircraft"], dict):
        data = data["aircraft"]
    return Aircraft.model_validate(data)


__all__ = ["Aircraft", "Category", "Regulation", "GustVelocities", "load_aircraft"]
