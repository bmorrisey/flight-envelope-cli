#!/usr/bin/env python
"""Compare flight-envelope results with published reference data.

Usage::

    python validation/run_validation.py            # from the repository root (or anywhere)

Reads ``validation/reference_data.yaml``, computes the corresponding package results,
prints a comparison table, writes ``validation/results.json`` and renders overlay figures to
``docs/images/validation_*.png``. Published values are drawn as distinct markers on top of
the package's own renderers (``render_vn`` / ``render_turn``), which consume ``EnvelopeData``.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import matplotlib as mpl
import numpy as np
import yaml
from matplotlib.figure import Figure
from matplotlib.legend import Legend
from matplotlib.lines import Line2D

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flight_envelope import OutputUnits, load_aircraft, turn_envelope, vn_envelope  # noqa: E402
from flight_envelope.aero.turn import load_factor_from_bank, turn_rate, turn_rate_from_bank  # noqa: E402
from flight_envelope.palette import PALETTE  # noqa: E402
from flight_envelope.plotting import style  # noqa: E402
from flight_envelope.plotting.turn_plot import render_turn  # noqa: E402
from flight_envelope.plotting.vn_plot import render_vn  # noqa: E402
from flight_envelope.units import FT, KT, LBF, SpeedType  # noqa: E402

REF_FILE = ROOT / "validation" / "reference_data.yaml"
RESULTS_FILE = ROOT / "validation" / "results.json"
IMG_DIR = ROOT / "docs" / "images"
DPI = 150
THEME = "light"

# Published-value marker styles (deliberately not palette tokens: they must stand apart
# from everything the package draws).
PUB = dict(marker="X", ms=10, mfc="#C026D3", mec="white", mew=1.0, ls="none", zorder=12)
PUB_CHART = dict(marker="s", ms=8, mfc="none", mec="#C026D3", mew=1.8, ls="none", zorder=12)
PUB_LINE = dict(color="#C026D3", lw=1.3, ls=(0, (4, 2)), zorder=11)


# ---------------------------------------------------------------------------
# Package results
# ---------------------------------------------------------------------------
def _lb_to_kg(lb: float) -> float:
    return lb * LBF / 9.80665


class Case:
    """Lazily computed package results for one reference case."""

    def __init__(self, spec: dict):
        self.spec = spec
        self.altitude_m = float(spec.get("altitude_ft", 0.0)) * FT
        self.ac = load_aircraft(ROOT / spec["aircraft"]) if "aircraft" in spec else None
        self._vn = self._turn = None

    @property
    def vn(self):
        if self._vn is None:
            self._vn = vn_envelope(self.ac, self.altitude_m, OutputUnits(speed="kt", speed_type="eas"))
        return self._vn

    @property
    def turn(self):
        if self._turn is None:
            self._turn = turn_envelope(self.ac, self.altitude_m,
                                       OutputUnits(speed="kt", speed_type=SpeedType.TAS))
        return self._turn

    def vn_at_mass(self, lb: float):
        ac = self.ac.with_overrides(mass=_lb_to_kg(lb))
        return vn_envelope(ac, self.altitude_m, OutputUnits(speed="kt", speed_type="eas"))[1]


def std_rate_bank_deg(v_kt: float, rate_deg_s: float = 3.0) -> float:
    """Bank angle giving ``rate_deg_s`` at ``v_kt`` TAS, by inverting the package's
    ``turn_rate_from_bank`` numerically (monotonic in phi)."""
    phi = np.radians(np.linspace(0.01, 89.0, 200_001))
    om = np.degrees(turn_rate_from_bank(v_kt * KT, phi))
    return float(np.degrees(np.interp(rate_deg_s, om, phi)))


def ours(case: Case, key: str):
    """Resolve a reference's ``ours`` selector to a package value."""
    head, _, rest = key.partition(".")
    if head == "vn":
        raw, env = case.vn
        kind, _, name = rest.partition(".")
        if kind == "summary":
            return env.summary[name].value
        if kind == "key_point_x":
            return env.key_points[name].x
        if kind == "key_point_y":
            return env.key_points[name].y
        if kind == "gust_neg":  # symmetric gust line: 1 - dn
            return 2.0 - env.summary[f"n_gust_{name}"].value
        if kind == "flag":
            if name == "gust_governs_positive":
                return bool(raw.gust_governs_positive)
            if name == "va_above_vc":
                return bool(raw.speeds["va"] > raw.speeds["vc"])
        if kind == "ratio":  # va:2200lb/2550lb
            q, _, w = name.partition(":")
            a, b = (float(s.removesuffix("lb")) for s in w.split("/"))
            return case.vn_at_mass(a).summary[q].value / case.vn_at_mass(b).summary[q].value
        if kind == "at_mass":
            q, _, w = name.partition(":")
            return case.vn_at_mass(float(w.removesuffix("lb"))).summary[q].value
    if head == "turn":
        _, env = case.turn
        kind, _, name = rest.partition(".")
        if kind == "summary":
            return env.summary[name].value
    if head == "kin":
        fn, _, arg = rest.partition(":")
        if fn == "load_factor_from_bank":
            return float(load_factor_from_bank(math.radians(float(arg))))
        if fn == "std_rate_bank":
            return std_rate_bank_deg(float(arg))
    raise KeyError(f"unknown selector {key!r}")


def evaluate(ref: dict, val) -> dict:
    r = ref["value"]
    out = {"quantity": ref["quantity"], "reference": r, "ours": val, "unit": ref["unit"],
           "confidence": ref["confidence"], "check": ref["check"], "source": ref["source"],
           "locator": ref.get("locator", "")}
    if isinstance(r, bool):
        out["error_pct"] = None
        out["pass"] = (val == r)
        return out
    err = (val - r) / abs(r) * 100.0 if r else None
    out["ours"] = round(float(val), 5)
    out["error_pct"] = None if err is None else round(err, 3)
    out["error_abs"] = round(float(val) - r, 5)
    c = ref["check"]
    if c == "abs":
        out["pass"] = abs(val - r) <= ref["tol_abs"] + 1e-9
        out["tolerance"] = f"±{ref['tol_abs']:g} {ref['unit']}"
    elif c == "pct":
        out["pass"] = abs(err) <= ref["tol_pct"]
        out["tolerance"] = f"±{ref['tol_pct']:g} %"
    elif c == "ge":
        out["pass"] = val >= r - 1e-9
        out["tolerance"] = "ours ≥ ref"
    elif c == "le":
        out["pass"] = val <= r + 1e-9
        out["tolerance"] = "ours ≤ ref"
    else:
        out["pass"] = None
        out["tolerance"] = "info"
    return out


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def _add_second_legend(ax, handles, title="Published reference values", loc="upper left"):
    """Add a legend for the published markers *inside* the axes, keeping the package's own
    legend (outside, right) as the axes legend so constrained layout still reserves room."""
    p = PALETTE[THEME]
    leg = Legend(ax, handles, [h.get_label() for h in handles], loc=loc, fontsize=7.8,
                 title=title, title_fontsize=8.2, frameon=True, framealpha=0.95,
                 facecolor=p["background"], edgecolor="#C026D3")
    leg.set_zorder(20)
    ax.add_artist(leg)
    return leg


def _tag(ax, x, y, text, dx=6, dy=6, ha="left", va="bottom"):
    ax.annotate(text, (x, y), xytext=(dx, dy), textcoords="offset points", fontsize=7.6,
                color="#86198F", ha=ha, va=va, zorder=13,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#C026D3", lw=0.5, alpha=0.9))


def fig_vn_purdue(case: Case, refs: dict[str, dict]) -> Path:
    _, env = case.vn
    with mpl.rc_context(style.rc_params(THEME)):
        fig = render_vn(env, THEME)
        ax = fig.axes[0]
        pts = [  # (x, y, text, offset)
            (94, 1.0, "94", (-4, 8, "right")), (110, -1.0, "110", (6, -12, "left")),
            (179, 3.58, "179 kt / 3.58 g", (-8, -16, "right")), (132, -1.43, "132 kt / −1.43 g", (10, 10, "left")),
        ]
        for x, y, t, (dx, dy, ha) in pts:
            ax.plot([x], [y], **PUB)
            _tag(ax, x, y, t, dx, dy, ha, "center")
        chart = [(177, 2.62), (177, -0.62), (240, 2.09), (240, -0.09)]
        for x, y in chart:
            ax.plot([x], [y], **PUB_CHART)
        _tag(ax, 177, 2.62, "2.62", 8, 0, "left", "center")
        _tag(ax, 177, -0.62, "−0.62", 8, 0, "left", "center")
        _tag(ax, 240, 2.09, "2.09", 9, 0, "left", "center")
        _tag(ax, 240, -0.09, "−0.09", 9, 0, "left", "center")
        handles = [
            Line2D([], [], label="Published (printed): $V_S$, $V_{S-}$, $V_A$, $V_G$, $n_{lim}$", **PUB),
            Line2D([], [], label="Published (read from chart): gust-line ends", **PUB_CHART),
        ]
        _add_second_legend(ax, handles, title="Purdue AAE 451 Aircraft Design book, V-n example",
                           loc="upper left")
        fig.suptitle("Validation — V-n worked example (Purdue AAE 451) vs flight_envelope",
                     x=0.01, ha="left", fontsize=14, fontweight="bold")
    return _save(fig, "validation_vn_purdue_example.png")


def fig_vn_c172(case: Case, name: str, title: str) -> Path:
    _, env = case.vn
    with mpl.rc_context(style.rc_params(THEME)):
        fig = render_vn(env, THEME)
        ax = fig.axes[0]
        y0, y1 = ax.get_ylim()
        vd_min = 160 / 0.9
        xl0, xl1 = ax.get_xlim()
        ax.set_xlim(xl0, max(xl1, vd_min * 1.08))
        ax.plot([51], [1.0], **PUB)
        _tag(ax, 51, 1.0, "$V_{S1}$ 51 KCAS", 6, 10, "left", "bottom")
        ax.plot([97], [3.8], **PUB)
        _tag(ax, 97, 3.8, "$V_A$ 97 KCAS", 0, -12, "center", "top")
        for x, lbl in ((126, "$V_{NO}$ 126 KCAS"), (160, "$V_{NE}$ 160 KCAS")):
            ax.plot([x, x], [y0, y1], **PUB_LINE)
            ax.text(x, y1 - 0.02 * (y1 - y0), lbl + " ", rotation=90, fontsize=7.6, color="#86198F",
                    ha="right", va="top", zorder=13)
        ax.plot([vd_min], [0.0], marker="|", ms=16, mew=2.2, color="#C026D3", ls="none", zorder=12)
        _tag(ax, vd_min, 0.0, "min $V_D$ =\n$V_{NE}$/0.9 = 178", 0, 12, "center", "bottom")
        handles = [
            Line2D([], [], label="POH speed (KCAS ≈ KEAS at SL)", **PUB),
            Line2D([], [], label="POH $V_{NO}$ / $V_{NE}$", **PUB_LINE),
            Line2D([], [], marker="|", ms=12, mew=2, color="#C026D3", ls="none",
                   label="Minimum $V_D$ implied by $V_{NE}$ (14 CFR 23.1505)"),
        ]
        _add_second_legend(ax, handles, title="Published: Cessna 172R POH, 2450 lb", loc="upper left")
        fig.suptitle(title, x=0.01, ha="left", fontsize=14, fontweight="bold")
    return _save(fig, name)


def fig_turn_c172(case: Case) -> Path:
    raw, env = case.turn
    with mpl.rc_context(style.rc_params(THEME)):
        fig = render_turn(env, THEME)
        ax = fig.axes[0]
        x0, x1 = ax.get_xlim()
        # Standard-rate turn (FAA IFH): 3 deg/s
        ax.plot([x0, x1], [3.0, 3.0], **PUB_LINE)
        ax.text(x1, 3.0, "standard rate 3°/s (FAA IFH) ", fontsize=7.6, color="#86198F", ha="right",
                va="bottom", zorder=13)
        # POH V_A plotted at the n = 3.8 g turn rate (kinematics from the package)
        om_va = float(np.degrees(turn_rate(97 * KT, 3.8)))
        ax.plot([97], [om_va], **PUB)
        _tag(ax, 97, om_va, f"POH $V_A$ 97 KCAS @ 3.8 g\n→ {om_va:.1f}°/s", 10, 18, "left", "bottom")
        ax.plot([51], [0.0], **PUB)
        _tag(ax, 51, 0.0, "POH $V_{S1}$ 51 KCAS", 34, 26, "left", "bottom")
        y0, y1 = ax.get_ylim()
        ax.plot([160, 160], [y0, y1], **PUB_LINE)
        ax.text(160, 0.86 * y1, "POH $V_{NE}$ 160 KCAS ", rotation=90, fontsize=7.6, color="#86198F",
                ha="right", va="center", zorder=13)
        handles = [
            Line2D([], [], label="Cessna 172R POH speed (KCAS ≈ KTAS at SL)", **PUB),
            Line2D([], [], label="POH $V_{NE}$ / FAA standard-rate turn", **PUB_LINE),
        ]
        leg = _add_second_legend(ax, handles, title="Published references", loc="lower center")
        leg.set_bbox_to_anchor((0.56, 0.07), transform=ax.transAxes)
        fig.suptitle("Validation — Cessna 172R (POH-calibrated) doghouse at sea level",
                     x=0.01, ha="left", fontsize=14, fontweight="bold")
    return _save(fig, "validation_turn_c172r.png")


def fig_kinematics(mass_case: Case) -> Path:
    p = PALETTE[THEME]
    with mpl.rc_context(style.rc_params(THEME)):
        fig = Figure(figsize=(13.5, 4.6), layout="constrained")
        a1, a2, a3 = fig.subplots(1, 3)

        phi = np.linspace(0, 80, 400)
        a1.plot(phi, load_factor_from_bank(np.radians(phi)), color=p["cobalt"], lw=2,
                label="flight_envelope: n = 1/cos φ")
        a1.plot([60], [2.0], **PUB, label="Published: FAA PHAK, 60° → 2 G")
        a1.set(xlabel="Bank angle φ [deg]", ylabel="Load factor n [g]", title="Load factor in a level turn",
               ylim=(0.8, 6.0))
        a1.legend(loc="upper left", fontsize=8)

        v = np.linspace(60, 200, 141)
        exact = [std_rate_bank_deg(x) for x in v]
        a2.plot(v, exact, color=p["cobalt"], lw=2, label="flight_envelope (exact, 3°/s)")
        a2.plot(v, 0.15 * v, color=p["muted"], lw=1.2, ls="--", label="FAA IFH rule: 15 % of TAS")
        a2.plot([100, 120], [15, 18], **PUB, label="Published: IFH examples (100 kt→15°, 120 kt→18°)")
        a2.set(xlabel="True airspeed [kt]", ylabel="Bank angle [deg]", title="Standard-rate turn bank angle")
        a2.legend(loc="upper left", fontsize=8)

        w = np.linspace(1800, 2600, 41)
        va = np.array([mass_case.vn_at_mass(x).summary["va"].value for x in w])
        va_ref = mass_case.vn_at_mass(2550).summary["va"].value
        a3.plot(w, va / va_ref, color=p["cobalt"], lw=2, label="flight_envelope: $V_A$/$V_A$(2550 lb)")
        a3.plot([2550, 2200, 1900], [1.0, 98 / 105, 90 / 105], **PUB,
                label="Published: C172S POH 105/98/90 KIAS")
        a3.set(xlabel="Weight [lb]", ylabel="$V_A$ / $V_A$(2550 lb)", title="Maneuvering speed vs weight")
        a3.legend(loc="upper left", fontsize=8)
        fig.suptitle("Validation — kinematics and weight scaling vs FAA handbooks and Cessna POH",
                     x=0.01, ha="left", fontsize=13, fontweight="bold")
    return _save(fig, "validation_kinematics.png")


def _save(fig: Figure, name: str) -> Path:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    path = IMG_DIR / name
    fig.savefig(path, dpi=DPI)
    return path


# ---------------------------------------------------------------------------
def main() -> int:
    spec = yaml.safe_load(REF_FILE.read_text(encoding="utf-8"))
    sources = spec["sources"]
    results, cases = [], {}
    for cs in spec["cases"]:
        case = Case(cs)
        cases[cs["id"]] = case
        rows = [evaluate(r, ours(case, r["ours"])) for r in cs["references"]]
        results.append({"id": cs["id"], "title": cs["title"], "aircraft": cs.get("aircraft"),
                        "altitude_ft": cs.get("altitude_ft"), "strength": cs.get("strength", "").strip(),
                        "comparisons": rows})

    # ----- table
    w = 46
    print(f"{'quantity':<{w}} {'reference':>10} {'ours':>10} {'unit':>6} {'err %':>8}  result   confidence")
    for res in results:
        print(f"\n== {res['title']}")
        for r in res["comparisons"]:
            ref, val = r["reference"], r["ours"]
            fmt = (lambda x: str(x)) if isinstance(ref, bool) else (lambda x: f"{x:.4g}")
            e = "" if r["error_pct"] is None else f"{r['error_pct']:+.2f}"
            verdict = {True: "PASS", False: "FAIL", None: "info"}[r["pass"]]
            print(f"{r['quantity'][:w]:<{w}} {fmt(ref):>10} {fmt(val):>10} {r['unit']:>6} {e:>8}  "
                  f"{verdict:<7}  {r['confidence']}")

    n_pass = sum(1 for res in results for r in res["comparisons"] if r["pass"] is True)
    n_fail = sum(1 for res in results for r in res["comparisons"] if r["pass"] is False)
    print(f"\n{n_pass} pass, {n_fail} fail")

    # ----- figures
    figs = [
        fig_vn_purdue(cases["purdue_vn"], {}),
        fig_vn_c172(cases["c172_example_vs_172r"], "validation_vn_c172_example.png",
                    "Validation — examples/cessna172.yaml vs Cessna 172R POH"),
        fig_vn_c172(cases["c172r_calibrated"], "validation_vn_c172r_calibrated.png",
                    "Validation — Cessna 172R (POH-calibrated) vs Cessna 172R POH"),
        fig_turn_c172(cases["c172r_calibrated"]),
        fig_kinematics(cases["c172s_weight_scaling"]),
    ]
    for f in figs:
        print("wrote", f.relative_to(ROOT))

    import flight_envelope
    RESULTS_FILE.write_text(json.dumps({
        "generator": "validation/run_validation.py",
        "package_version": getattr(flight_envelope, "__version__", "unknown"),
        "summary": {"pass": n_pass, "fail": n_fail},
        "sources": sources,
        "cases": results,
        "figures": [str(f.relative_to(ROOT)) for f in figs],
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", RESULTS_FILE.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
