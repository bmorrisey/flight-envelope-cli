# flight-envelope (`flightenv`)

Compute, visualise and export aircraft flight-performance envelopes:

* **V-n diagram**: maneuver envelope, Pratt-formula gust envelope and the combined boundary per 14 CFR 23.333–23.341 / 25.333–25.341, with design speeds (V_S1, V_A, V_G, V_B, V_C, V_D), limit, ultimate and failure zones.
* **Turn performance (doghouse / E-M) diagram**: instantaneous (C_L,max and structural) limits, the q-limit, the sustained (P_s = 0) boundary, P_s contours, bank-angle and turn-radius isolines, and the corner speed.

The package is split into four layers that are kept separate:

```
aero/      pure SI physics, numpy-vectorised  → VnResult / TurnResult (raw arrays)
envelopes  SI → display units                 → EnvelopeData (Pydantic, the data contract)
plotting/  Matplotlib + Plotly renderers      ← consume EnvelopeData only
cli/       Typer + Rich
```

The renderers draw only what is in `EnvelopeData`. A client that re-plots the exported JSON (D3, Plotly, a CAD tool, …) therefore gets exactly the same chart.

## Install

```bash
pip install -e ".[dev]"        # or ".[interactive]" for Plotly HTML output only
```

## CLI

```bash
flightenv vn   -c examples/f16.yaml --altitude 0 -o vn_diagram.png -e vn_data.json
flightenv turn -c examples/cessna172.yaml --altitude 5000 --unit-speed knots -o turn_doghouse.png --interactive
flightenv info -c examples/f16.yaml
flightenv schema envelope -o envelope.schema.json     # JSON Schema of the export contract
```

| Option | Meaning |
|---|---|
| `-a/--altitude`, `--alt-unit ft\|m` | ISA pressure altitude (default ft) |
| `-m/--mass "2200 lb"` | Override the configured mass |
| `-u/--unit-speed kt\|m/s\|km/h\|mph`, `--speed-type eas\|tas` | Speed axis (V-n defaults to EAS, turn defaults to TAS) |
| `-l/--unit-length m\|ft` | Turn radius and P_s units (turn) |
| `--bank-angles`, `--radii`, `--ps` | Overlay contour levels (turn); defaults are chosen automatically |
| `-o/--output` | `.png` / `.svg` / `.pdf` (Matplotlib, `--dpi`, default 300) or `.html` (Plotly) |
| `-e/--export-data` | `.json` or `.csv`; repeatable |
| `-t/--theme light\|dark`, `-i/--interactive`, `-q/--quiet` | |

Each command prints Rich tables of the key speeds, load factors (with their regulatory basis) and turn metrics, plus any warnings (gust-governed loads, V_D < 1.25 V_C, …).

## Aircraft configuration

YAML or JSON, optionally wrapped in a top-level `aircraft:` key. Bare numbers are SI. Strings can carry units: `"2450 lb"`, `"174 ft^2"`, `"126 kt"`, `"180 hp"`, `"29000 lbf"`, `"0.084 /deg"`. Design speeds are **equivalent** airspeeds.

| Field | Notes |
|---|---|
| `name`, `category` | `normal`, `utility`, `aerobatic`, `commuter`, `transport`, `military` |
| `mass`, `wing_area`, `wing_chord` (MAC), `aspect_ratio`, `oswald_efficiency` | |
| `cd0`, `cl_max_clean`, `cl_min_clean`, `lift_curve_slope` | Parabolic polar; C_Lα in 1/rad |
| `max_thrust_sl` (jet, `T = T_SL σ^m`, `thrust_lapse_exponent`, default 0.7) | |
| `max_power_sl` + `prop_efficiency` (prop, `T = η P(σ)/V`, Gagg-Ferrar lapse) | With a prop, `max_thrust_sl` becomes the static-thrust cap |
| `v_c`, `v_d` (default 1.25 V_C), `v_b` (FAR 25: derived from the 66 ft/s line if omitted) | |
| `n_pos_limit`, `n_neg_limit` | If omitted: 23.337 / 25.337 (`2.1 + 24000/(W+10000)`, clamped to 2.5–3.8; 4.4 utility; 6.0 aerobatic; negative −0.4n, −0.5n or −1.0) |
| `n_neg_at_vd`, `ultimate_factor` (1.5), `gust:` (U_de overrides) | |

Example files: `examples/cessna172.yaml` (FAR 23 normal, propeller), `examples/f16.yaml` (military jet), `examples/regional_turboprop.yaml` (FAR 25, exercises V_B).

## Python API

```python
from flight_envelope import load_aircraft, vn_envelope, turn_envelope, OutputUnits
from flight_envelope.plotting.vn_plot import plot_vn

ac = load_aircraft("examples/cessna172.yaml")
raw, data = vn_envelope(ac, altitude_m=0.0, units=OutputUnits(speed="kt", speed_type="eas"))
raw.speeds["va"]              # SI engine output (m/s EAS)
data.key_points["Va"]         # KeyPoint(x=102.5, y=3.8, label='$V_A$', ...)
data.to_json("vn.json"); data.to_csv("vn.csv")
plot_vn(data, "vn.svg", theme="dark")
```

## Data contract (`EnvelopeData`)

```jsonc
{
  "schema_version": "1.0",
  "envelope_type": "vn_diagram" | "turn_performance",
  "aircraft_name": "...", "title": "...",
  "flight_conditions": {"altitude_m", "altitude_ft", "density_kg_m3", "density_ratio", "mass_kg", "regulation", ...},
  "units": {"speed": "KEAS", ...},
  "x_axis": {"label", "unit", "min", "max"}, "y_axis": {...},
  "key_points": {"Va": {"x", "y", "label", "description"}, ...},
  "summary":    {"va": {"value", "unit", "label", "description"}, ...},
  "boundaries": [CurveSeries], "contours": [CurveSeries],
  "regions":    [{"id", "label", "role", "points", "color_hint", "opacity"}],
  "safe_region_polygon": [{"x", "y"}],
  "warnings": [...],
  "palette": {"light": {"cobalt": "#1D4ED8", ...}, "dark": {...}}
}
```

`CurveSeries` = `{id, label, role, x_unit, y_unit, points[{x,y}], line_style, line_width, color_hint, inline_label, show_in_legend}`. `color_hint` is a palette token, so clients resolve it with `palette[theme][token]`. Labels use Matplotlib mathtext (`$V_A$`). CSV export is long-format: `kind,id,label,index,x,y,x_unit,y_unit`.

The stable series ids are:

* **V-n:** `maneuver_envelope`, `combined_envelope`, `gust_envelope`, `positive_stall`, `negative_stall`, `ultimate_pos/neg`, `gust_{vb,vc,vd}_{pos,neg}`; regions `safe_envelope`, `stall_pos/neg`, `structural_damage_*`, `structural_failure_*`, `overspeed`.
* **Turn:** `instantaneous_envelope`, `aerodynamic_limit`, `structural_limit`, `sustained_limit`, `q_limit`, `bank_<deg>`, `radius_<R><unit>`, `ps_<value>`; regions `safe_envelope`, `energy_depletion`, `stall_region`, `overstress`, `overspeed`.

## Modelling notes and limitations

* ISA, 0–86 km. Input altitude is geometric and is converted to geopotential.
* The V-n maneuver envelope is altitude-independent in EAS. Altitude enters through the gust mass ratio μ_g (actual ρ) and the U_de reduction above 20 000 ft.
* Gusts use the Pratt formula (FAR 23.341, and pre-Amdt 25-86 FAR 25.341). The tuned 1-cos discrete gust of current Part 25 is not modelled.
* Ultimate load = `ultimate_factor` × the governing design limit (maneuver ∪ gust).
* Turn performance assumes an incompressible parabolic polar and no Mach effect on thrust. Results above roughly M 0.7 are optimistic. "Sustained" means a level, coordinated turn.

## Tests

```bash
pytest -q
```

The suite checks ISA against the US Standard Atmosphere 1976, the FAR 23 load-factor formula and the gust increment against the independent imperial FAR 23 form (`498·W/S`), and turn kinematics against textbook values. It also covers sustained-turn force balance, the JSON/CSV round-trips and CLI smoke tests.
