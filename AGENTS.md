# AGENTS.md

Guide for AI coding agents (and humans) working in this repository. Read it before changing code.

## What this project is

`flight-envelope` (CLI: `flightenv`) computes aircraft **V-n diagrams** (FAR 23/25 maneuver and gust envelopes) and **turn-performance / doghouse / E-M diagrams**. It renders them with Matplotlib/Plotly and exports the underlying curves as a JSON/CSV data contract. The output is engineering data, so correctness and traceability to a reference matter more than features.

## Commands

```bash
pip install -e ".[dev]"            # editable install with pytest + plotly
pytest -q                          # full suite, ~4 s; must stay green
pytest tests/test_vn.py -q         # one module
python scripts/make_gallery.py     # regenerate docs/images/*.png (README gallery)
python validation/run_validation.py  # published-data comparison → docs/images/validation_*.png

flightenv vn   -c examples/cessna172.yaml -o /tmp/vn.png -e /tmp/vn.json
flightenv turn -c examples/f16.yaml -a 15000 -o /tmp/turn.png --theme dark
flightenv info -c examples/regional_turboprop.yaml
flightenv schema envelope
```

Write scratch output to `out/` (git-ignored) or a temp dir, never the repo root.

## Architecture: rules that must hold

```
aero/  (SI physics)  →  envelopes.py (SI → display units)  →  EnvelopeData  →  plotting/  &  JSON/CSV
                                                                 ↑
                                                             cli/main.py drives the pipeline
```

1. **`aero/` is pure SI, numpy-vectorised, and has no I/O.** It knows nothing about plotting, display units or the CLI. Speeds are m/s (V-n works in EAS, turn works in TAS), rates are rad/s, forces are N. Engines return frozen dataclasses (`VnResult`, `TurnResult`) holding raw arrays.
2. **`envelopes.py` is the only place SI is converted to display units.** If you need a new unit or label, add it to `units.py` / `OutputUnits` and use it here.
3. **Renderers draw only from `EnvelopeData`.** `plotting/*` must never import from `aero/` or recompute physics. If a chart needs a new line, region or point, add it to the data model in `envelopes.py` first; then exported JSON clients get it too.
4. **Series/region/key-point `id`s are a public API** (listed in the README "Stable ids" table). Don't rename or remove them. Add new ids instead, and update the README table and tests. A breaking change to `EnvelopeData` needs a `SCHEMA_VERSION` bump in `models/outputs.py`.
5. **Colours are palette tokens** (`palette.py`), never hex in builder code. Both `light` and `dark` must define every token.
6. Plotting uses the object-oriented `matplotlib.figure.Figure`, **not `pyplot`**, so it is backend-free and has no global state. Keep it that way.

## Physics conventions

- **Design speeds in configs are EAS.** V-n math uses ρ₀ with EAS. The gust mass ratio μ_g uses the *actual* density at altitude. Turn math uses TAS and actual ρ.
- **Altitude:** user input is geometric. `isa()` converts to geopotential internally.
- Gravity is `units.G0 = 9.80665` exactly. Don't hard-code `9.81` or `1.225`; use `G0`, `atmosphere.RHO0` and `isa()`.
- Regulatory logic (limit load factors, negative-limit taper, gust velocities, V_B) lives in `aero/vn.py`, with the CFR section cited in the docstring. If you change regulatory behaviour, cite the paragraph and add a test.
- Known simplifications (keep them documented in the README "Modelling notes"): incompressible parabolic polar, no Mach effects on thrust, Pratt gust formula (not the Part 25 tuned 1-cos gust), standard day, constant weight.
- Never invent "reference" numbers. Validation values must cite a source (see `validation/reference_data.yaml`).

## Inputs (`models/aircraft.py`)

- `Aircraft` is a strict Pydantic v2 model (`extra="forbid"`). Typos in YAML fail loudly. Keep that.
- Numeric fields accept SI numbers or unit strings through `units.parse_quantity`. To support a new unit, add it to `_UNIT_TABLES` and add a test in `tests/test_units.py`.
- New optional fields need a default that reproduces current behaviour, so existing YAML files keep working.

## Testing expectations

- Every physics change comes with a test against an **independent** reference: a closed-form value, a published table, or an alternative formulation (e.g. the imperial FAR form of the gust equation). A test that only re-runs the same code path proves nothing.
- Tests live in `tests/`, use the fixtures in `tests/conftest.py` (`cessna`, `f16`, `turboprop`, `any_aircraft`), and must stay fast. Use `--dpi 60` / small `n_points` in render or CLI tests.
- CLI tests use `typer.testing.CliRunner` and write only into `tmp_path`.

## Visual changes

Charts are part of the product. After touching `plotting/`, `palette.py` or anything that moves curves:

1. Run `python scripts/make_gallery.py`.
2. **Look at every image** in `docs/images/`, in both themes. Check that labels and the legend don't overlap, nothing is cut off, and the annotations stay inside the axes. `style.declutter()` handles annotation collisions; extend it rather than hand-tuning offsets for one aircraft.
3. Commit the regenerated images together with the code change.

## Adding things

- **New aircraft example:** add `examples/<name>.yaml` with a header comment saying what it exercises and that the values are generic. Add it to the README table and, if it is useful, to `scripts/make_gallery.py`.
- **New envelope type** (e.g. a payload-range or altitude-Mach envelope):
  1. Engine in `aero/<name>.py` returning a frozen result dataclass (SI).
  2. Builder in `envelopes.py` that turns it into `EnvelopeData` (add a new `envelope_type` literal).
  3. Renderer in `plotting/<name>_plot.py`. `interactive.py` is generic and should work unchanged.
  4. CLI command in `cli/main.py` using `_run_pipeline`.
  5. Tests for each layer, plus a README section.
- **New CLI option:** use Typer options with help text, validate input, exit with code 2 on bad input, and print errors to stderr through `err`.

## Style

- Python ≥ 3.10, `from __future__ import annotations`, type hints on public functions, numpy-style vectorisation (no per-point Python loops in engines).
- Docstrings state units and, for regulatory or physical formulas, the equation and its source.
- Keep modules focused. Don't add dependencies without a strong reason. Plotly stays an optional extra.

## Git and repository hygiene

- Don't commit `out/`, caches or scratch files. Do commit regenerated `docs/images/*` when visuals change.
- Don't commit third-party copyrighted chart images or text (manuals, textbooks). Validation re-plots *numbers* extracted from cited sources.
- Commit messages: imperative subject line (≤ 72 chars), with a body explaining *why* when the change isn't obvious.
