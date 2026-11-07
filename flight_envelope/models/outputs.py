"""Serializable envelope data contract (JSON / CSV) for client-side plotting.

Everything the built-in renderers draw comes from an :class:`EnvelopeData`
instance, so a client re-plotting the exported JSON (D3, Plotly, ...) gets
exactly the same picture.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Literal, Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..palette import PALETTE

SCHEMA_VERSION = "1.0"

LineStyle = Literal["solid", "dashed", "dotted", "dashdot"]


class Point2D(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: float
    y: float


class KeyPoint(Point2D):
    """A characteristic point (corner speed, stall speed, ...)."""

    label: str = Field("", description="Short label; may contain matplotlib mathtext, e.g. '$V_A$'")
    description: str = ""


class CurveSeries(BaseModel):
    id: str = Field(description="Stable identifier, e.g. 'positive_stall', 'gust_vc_pos'")
    label: str = Field(description="Human-readable legend label (mathtext allowed)")
    role: str = Field("", description="Semantic role, e.g. 'stall', 'structural', 'gust', 'bank_angle'")
    x_unit: str
    y_unit: str
    points: List[Point2D]
    line_style: LineStyle = "solid"
    line_width: float = 1.5
    color_hint: str = Field("slate", description="Palette token (see EnvelopeData.palette)")
    inline_label: Optional[str] = Field(None, description="Text to place along the curve")
    show_in_legend: bool = True


class Region(BaseModel):
    """A filled polygon (operating envelope, stall zone, structural failure zone, ...)."""

    id: str
    label: str
    role: str = ""
    points: List[Point2D]
    color_hint: str
    opacity: float = Field(0.15, ge=0, le=1)
    hatch: Optional[str] = None
    show_in_legend: bool = True


class ScalarResult(BaseModel):
    value: float
    unit: str
    label: str
    description: str = ""


class AxisSpec(BaseModel):
    label: str
    unit: str
    min: float
    max: float


class EnvelopeData(BaseModel):
    schema_version: str = SCHEMA_VERSION
    envelope_type: Literal["vn_diagram", "turn_performance"]
    aircraft_name: str
    title: str = ""
    flight_conditions: Dict[str, Any]
    units: Dict[str, str]
    x_axis: AxisSpec
    y_axis: AxisSpec
    key_points: Dict[str, KeyPoint]
    summary: Dict[str, ScalarResult] = Field(default_factory=dict)
    boundaries: List[CurveSeries]
    contours: Optional[List[CurveSeries]] = None
    regions: List[Region] = Field(default_factory=list)
    safe_region_polygon: List[Point2D]
    warnings: List[str] = Field(default_factory=list)
    palette: Dict[str, Dict[str, str]] = Field(default_factory=lambda: PALETTE)

    # -------------------------------------------------------------- helpers
    def series(self, series_id: str) -> CurveSeries:
        for s in [*self.boundaries, *(self.contours or [])]:
            if s.id == series_id:
                return s
        raise KeyError(series_id)

    def region(self, region_id: str) -> Region:
        for r in self.regions:
            if r.id == region_id:
                return r
        raise KeyError(region_id)

    # -------------------------------------------------------------- export
    def to_json(self, path: str | Path | None = None, indent: int = 2) -> str:
        text = self.model_dump_json(indent=indent)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    @classmethod
    def from_json(cls, path_or_text: str | Path) -> "EnvelopeData":
        p = Path(path_or_text) if not str(path_or_text).lstrip().startswith("{") else None
        text = p.read_text(encoding="utf-8") if p is not None else str(path_or_text)
        return cls.model_validate_json(text)

    def csv_rows(self) -> Iterable[dict[str, Any]]:
        """Long-format rows: one per point of every series, region and key point."""
        xu, yu = self.x_axis.unit, self.y_axis.unit
        for kind, items in (("boundary", self.boundaries), ("contour", self.contours or [])):
            for s in items:
                for i, p in enumerate(s.points):
                    yield {"kind": kind, "id": s.id, "label": s.label, "index": i,
                           "x": p.x, "y": p.y, "x_unit": s.x_unit, "y_unit": s.y_unit}
        for r in self.regions:
            for i, p in enumerate(r.points):
                yield {"kind": "region", "id": r.id, "label": r.label, "index": i,
                       "x": p.x, "y": p.y, "x_unit": xu, "y_unit": yu}
        for i, p in enumerate(self.safe_region_polygon):
            yield {"kind": "safe_region", "id": "safe_region", "label": "Safe operating envelope",
                   "index": i, "x": p.x, "y": p.y, "x_unit": xu, "y_unit": yu}
        for key, kp in self.key_points.items():
            yield {"kind": "key_point", "id": key, "label": kp.label, "index": 0,
                   "x": kp.x, "y": kp.y, "x_unit": xu, "y_unit": yu}

    def to_csv(self, path: str | Path) -> None:
        fields = ["kind", "id", "label", "index", "x", "y", "x_unit", "y_unit"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            writer.writerows(self.csv_rows())

    def export(self, path: str | Path) -> Path:
        """Export by file extension (.json or .csv)."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            self.to_json(path)
        elif suffix == ".csv":
            self.to_csv(path)
        else:
            raise ValueError(f"unsupported data export format {suffix!r} (use .json or .csv)")
        return path


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------
def to_points(x, y, decimals: int = 6) -> List[Point2D]:
    """Arrays -> list of Point2D, dropping non-finite samples."""
    xs = np.asarray(x, dtype=float)
    ys = np.asarray(y, dtype=float)
    out = []
    for xi, yi in zip(xs, ys):
        if math.isfinite(xi) and math.isfinite(yi):
            out.append(Point2D(x=round(float(xi), decimals), y=round(float(yi), decimals)))
    return out


def finite_segments(x, y) -> list[tuple[np.ndarray, np.ndarray]]:
    """Split (x, y) into contiguous runs of finite values."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    segments, start = [], None
    for i, flag in enumerate(ok):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            segments.append((x[start:i], y[start:i]))
            start = None
    if start is not None:
        segments.append((x[start:], y[start:]))
    return [s for s in segments if len(s[0]) >= 2]
