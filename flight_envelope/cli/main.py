"""``flightenv`` command-line interface (Typer + Rich)."""

from __future__ import annotations

import json
import math
import webbrowser
from enum import Enum
from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .. import __version__
from ..aero.atmosphere import isa
from ..aero.turn import compute_turn
from ..aero.vn import compute_vn, resolve_limits
from ..envelopes import build_turn_envelope, build_vn_envelope
from ..models.aircraft import Aircraft, load_aircraft
from ..models.outputs import EnvelopeData
from ..units import FT, KT, LBF, HP, LengthUnit, OutputUnits, SpeedType, parse_quantity, resolve_speed_unit

app = typer.Typer(
    name="flightenv",
    help="Compute, plot and export aircraft flight envelopes (V-n and turn performance).",
    no_args_is_help=True,
    rich_markup_mode="rich",
    add_completion=False,
)
console = Console()
err = Console(stderr=True)


class Theme(str, Enum):
    light = "light"
    dark = "dark"


class AltUnit(str, Enum):
    ft = "ft"
    m = "m"


def _version(value: bool):
    if value:
        console.print(f"flightenv {__version__}")
        raise typer.Exit()


@app.callback()
def main(version: bool = typer.Option(False, "--version", callback=_version, is_eager=True,
                                      help="Show version and exit.")):
    """flightenv — aircraft flight-envelope toolkit."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
ConfigOpt = typer.Option(..., "--config", "-c", exists=True, dir_okay=False, readable=True,
                         help="Aircraft YAML/JSON configuration.")
AltOpt = typer.Option(0.0, "--altitude", "-a", help="Pressure altitude (ISA).")
AltUnitOpt = typer.Option(AltUnit.ft, "--alt-unit", help="Unit of --altitude.")
MassOpt = typer.Option(None, "--mass", "-m", help="Override mass, e.g. '1000 kg' or '2200 lb'.")
OutOpt = typer.Option(None, "--output", "-o", help="Chart file (.png/.svg/.pdf, or .html for Plotly).")
ExportOpt = typer.Option(None, "--export-data", "-e", help="Export curve data (.json/.csv). Repeatable.")
ThemeOpt = typer.Option(Theme.light, "--theme", "-t", help="Chart colour theme.")
DpiOpt = typer.Option(300, "--dpi", min=50, max=1200, help="Raster export resolution.")
InteractiveOpt = typer.Option(False, "--interactive", "-i",
                              help="Also write a Plotly interactive HTML chart and open it.")
QuietOpt = typer.Option(False, "--quiet", "-q", help="Suppress tables and progress output.")


def _load(config: Path, mass: Optional[str]) -> Aircraft:
    try:
        ac = load_aircraft(config)
        if mass is not None:
            ac = ac.with_overrides(mass=parse_quantity(mass, "mass"))
        return ac
    except Exception as exc:  # pydantic.ValidationError, ValueError, yaml errors
        err.print(Panel(str(exc), title=f"[red]Invalid configuration: {config}", border_style="red"))
        raise typer.Exit(code=2) from exc


def _altitude_m(alt: float, unit: AltUnit) -> float:
    h = alt * FT if unit is AltUnit.ft else alt
    try:
        isa(h)
    except ValueError as exc:
        err.print(f"[red]Altitude out of range:[/] {exc}")
        raise typer.Exit(code=2) from exc
    return h


def _parse_floats(text: Optional[str], name: str) -> Optional[list[float]]:
    if text is None:
        return None
    try:
        return [float(t) for t in text.replace(";", ",").split(",") if t.strip()]
    except ValueError as exc:
        err.print(f"[red]--{name} must be a comma-separated list of numbers[/]")
        raise typer.Exit(code=2) from exc


def _units(speed: str, speed_type: SpeedType, length: LengthUnit = LengthUnit.M) -> OutputUnits:
    try:
        return OutputUnits(speed=resolve_speed_unit(speed), speed_type=speed_type, length=length)
    except ValueError as exc:
        err.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc


def _progress(quiet: bool) -> Progress:
    return Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=28), TextColumn("{task.completed}/{task.total}"),
                    TimeElapsedColumn(), console=console, transient=True, disable=quiet)


def _render(env: EnvelopeData, output: Optional[Path], theme: Theme, dpi: int) -> Optional[Path]:
    if output is None:
        return None
    if output.suffix.lower() == ".html":
        from ..plotting.interactive import write_html

        return write_html(env, output, theme.value)
    if env.envelope_type == "vn_diagram":
        from ..plotting.vn_plot import plot_vn

        return plot_vn(env, output, theme.value, dpi)
    from ..plotting.turn_plot import plot_turn

    return plot_turn(env, output, theme.value, dpi)


def _run_pipeline(env_builder, output, export_data, interactive, theme, dpi, quiet, default_stem):
    """Shared compute -> serialise -> render -> export stages with a progress bar."""
    exports = list(export_data or [])
    for path in exports:
        if path.suffix.lower() not in (".json", ".csv"):
            err.print(f"[red]--export-data must end in .json or .csv (got {path})[/]")
            raise typer.Exit(code=2)
    html_path = None
    if interactive:
        html_path = (output.with_suffix(".html") if output and output.suffix.lower() != ".html"
                     else output or Path(f"{default_stem}.html"))
    steps = 2 + (output is not None) + len(exports) + (html_path is not None and html_path != output)
    written: list[tuple[str, Path]] = []
    with _progress(quiet) as progress:
        task = progress.add_task("Computing envelope points", total=steps)
        result, env = env_builder(progress, task)
        if output is not None:
            progress.update(task, description=f"Rendering {output.name}")
            try:
                written.append(("chart", _render(env, output, theme, dpi)))
            except ValueError as exc:
                err.print(f"[red]{exc}[/]")
                raise typer.Exit(code=2) from exc
            progress.advance(task)
        if html_path is not None and html_path != output:
            progress.update(task, description=f"Rendering {html_path.name}")
            from ..plotting.interactive import write_html

            written.append(("interactive", write_html(env, html_path, theme.value)))
            progress.advance(task)
        for path in exports:
            progress.update(task, description=f"Exporting {path.name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(("data", env.export(path)))
            progress.advance(task)
    if html_path is not None:
        try:
            webbrowser.open(html_path.resolve().as_uri())
        except Exception:  # pragma: no cover - headless environments
            pass
    return result, env, written


def _print_written(written, quiet):
    if quiet:
        return
    for kind, path in written:
        console.print(f"[green]✔[/] {kind:<11} → [bold]{path}[/]")


def _print_warnings(env: EnvelopeData, quiet: bool):
    if env.warnings and not quiet:
        console.print(Panel("\n".join(f"• {w}" for w in env.warnings), title="Warnings",
                            border_style="yellow", expand=False))


def _fmt(v: float, nd: int = 1) -> str:
    return "—" if v is None or not math.isfinite(v) else f"{v:,.{nd}f}"


def _speed_table(title: str, rows: list[tuple[str, str, float]], sigma: float) -> Table:
    """rows: (symbol, description, EAS m/s)."""
    t = Table(title=title, title_style="bold", header_style="bold cyan", show_lines=False)
    for col in ("Speed", "Description"):
        t.add_column(col)
    for col in ("KEAS", "KTAS", "m/s EAS", "km/h EAS"):
        t.add_column(col, justify="right")
    for sym, desc, v in rows:
        t.add_row(sym, desc, _fmt(v / KT), _fmt(v / math.sqrt(sigma) / KT), _fmt(v), _fmt(v * 3.6))
    return t


# ---------------------------------------------------------------------------
# vn
# ---------------------------------------------------------------------------
@app.command("vn")
def vn_cmd(
    config: Path = ConfigOpt,
    altitude: float = AltOpt,
    alt_unit: AltUnit = AltUnitOpt,
    mass: Optional[str] = MassOpt,
    unit_speed: str = typer.Option("kt", "--unit-speed", "-u", help="Speed unit: kt|knots|m/s|km/h|mph."),
    speed_type: SpeedType = typer.Option(SpeedType.EAS, "--speed-type", help="Plot EAS or TAS."),
    output: Optional[Path] = OutOpt,
    export_data: Optional[List[Path]] = ExportOpt,
    theme: Theme = ThemeOpt,
    dpi: int = DpiOpt,
    interactive: bool = InteractiveOpt,
    points: int = typer.Option(600, "--points", min=50, max=20000, help="Speed-grid resolution."),
    quiet: bool = QuietOpt,
):
    """Compute the [bold]V-n diagram[/]: maneuver + gust envelopes (14 CFR 23/25)."""
    ac = _load(config, mass)
    h = _altitude_m(altitude, alt_unit)
    units = _units(unit_speed, speed_type)

    def build(progress, task):
        res = compute_vn(ac, h, n_points=points)
        progress.advance(task)
        progress.update(task, description="Serialising envelope data")
        env = build_vn_envelope(res, units)
        progress.advance(task)
        return res, env

    res, env, written = _run_pipeline(build, output, export_data, interactive, theme, dpi, quiet, "vn_diagram")
    if not quiet:
        sp = res.speeds
        rows = [("V_S1", "Stall speed (1 g)", sp["vs1"]), ("V_S,inv", "Inverted stall (-1 g)", sp["vs_neg"]),
                ("V_A", "Maneuvering / corner speed", sp["va"]), ("V_G", "Negative corner speed", sp["vg"])]
        if "vb" in sp:
            rows.append(("V_B", "Max gust intensity speed", sp["vb"]))
        rows += [("V_C", "Design cruise speed", sp["vc"]), ("V_D", "Design dive speed", sp["vd"])]
        console.print(_speed_table(f"V-n key speeds — {ac.name} @ {h / FT:,.0f} ft", rows, res.sigma))

        lt = Table(title="Load factors", title_style="bold", header_style="bold cyan")
        for col, j in (("Quantity", "left"), ("Positive", "right"), ("Negative", "right"), ("Basis", "left")):
            lt.add_column(col, justify=j)
        lt.add_row("Maneuver limit", f"{res.n_pos:+.2f} g", f"{res.n_neg:+.2f} g",
                   f"{res.limit_sources[0]} / {res.limit_sources[1]}")
        lt.add_row("Design limit (maneuver ∪ gust)", f"{res.n_design_pos:+.2f} g", f"{res.n_design_neg:+.2f} g",
                   "gust governs" if (res.gust_governs_positive or res.gust_governs_negative) else "maneuver governs")
        lt.add_row("Ultimate", f"{res.n_ult_pos:+.2f} g", f"{res.n_ult_neg:+.2f} g",
                   f"{ac.ultimate_factor:g} × design limit")
        for g in res.gust_lines:
            lt.add_row(f"Gust @ {g.key.upper()}  (U_de = {g.u_de / FT:.1f} ft/s)",
                       f"{1 + g.delta_n:+.2f} g", f"{1 - g.delta_n:+.2f} g", "Pratt formula")
        lt.add_row("Gust mass ratio μ_g / K_g", f"{res.mu_g:.2f}", f"{res.k_g:.3f}", "")
        console.print(lt)
        _print_warnings(env, quiet)
    _print_written(written, quiet)


# ---------------------------------------------------------------------------
# turn
# ---------------------------------------------------------------------------
@app.command("turn")
def turn_cmd(
    config: Path = ConfigOpt,
    altitude: float = AltOpt,
    alt_unit: AltUnit = AltUnitOpt,
    mass: Optional[str] = MassOpt,
    unit_speed: str = typer.Option("kt", "--unit-speed", "-u", help="Speed unit: kt|knots|m/s|km/h|mph."),
    speed_type: SpeedType = typer.Option(SpeedType.TAS, "--speed-type", help="Plot TAS or EAS."),
    unit_length: LengthUnit = typer.Option(LengthUnit.M, "--unit-length", "-l", help="Turn-radius unit."),
    bank_angles: Optional[str] = typer.Option(None, "--bank-angles", help="Bank isoclines [deg], e.g. '30,45,60'."),
    radii: Optional[str] = typer.Option(None, "--radii", help="Turn-radius isolines in --unit-length."),
    ps: Optional[str] = typer.Option(None, "--ps", help="P_s contour levels [m/s or ft/s], e.g. '-20,-50'."),
    output: Optional[Path] = OutOpt,
    export_data: Optional[List[Path]] = ExportOpt,
    theme: Theme = ThemeOpt,
    dpi: int = DpiOpt,
    interactive: bool = InteractiveOpt,
    points: int = typer.Option(600, "--points", min=50, max=20000, help="Speed-grid resolution."),
    quiet: bool = QuietOpt,
):
    """Compute the [bold]turn-performance (doghouse / E-M) diagram[/]."""
    ac = _load(config, mass)
    h = _altitude_m(altitude, alt_unit)
    units = _units(unit_speed, speed_type, unit_length)
    banks = _parse_floats(bank_angles, "bank-angles")
    if banks and any(not 0 < b < 90 for b in banks):
        err.print("[red]--bank-angles must lie in (0, 90) degrees[/]")
        raise typer.Exit(code=2)
    rads = _parse_floats(radii, "radii")
    ps_levels = _parse_floats(ps, "ps")
    if ps_levels is not None:
        ps_levels = [units.length_to_si(v) for v in ps_levels]

    def build(progress, task):
        res = compute_turn(ac, h, ps_levels=ps_levels, n_points=points, ps_unit=units.length_to_si(1.0))
        progress.advance(task)
        progress.update(task, description="Serialising envelope data")
        env = build_turn_envelope(res, units, banks, rads)
        progress.advance(task)
        return res, env

    res, env, written = _run_pipeline(build, output, export_data, interactive, theme, dpi, quiet, "turn_doghouse")
    if not quiet:
        lu = unit_length.value
        rf = units.length_from_si
        t = Table(title=f"Turn performance — {ac.name} @ {h / FT:,.0f} ft", title_style="bold",
                  header_style="bold cyan")
        for col, j in (("Quantity", "left"), ("Value", "right"), ("At speed", "right")):
            t.add_column(col, justify=j)
        sl = units.speed_label

        def spd(v_tas):
            v = v_tas * math.sqrt(res.sigma) if speed_type is SpeedType.EAS else v_tas
            return f"{units.speed_from_si(v):,.1f} {sl}"

        t.add_row("Stall speed (1 g)", "", spd(res.v_stall_1g))
        t.add_row("Corner speed V*", f"M {res.v_corner / res.speed_of_sound:.3f}", spd(res.v_corner))
        t.add_row("Max instantaneous turn rate", f"{math.degrees(res.omega_max_inst):.2f} °/s", spd(res.v_corner))
        t.add_row("Minimum turn radius", f"{rf(res.radius_min):,.0f} {lu}", spd(res.v_corner))
        t.add_row("Structural limit", f"{res.n_limit:.2f} g", "")
        if res.omega_max_sus > 0:
            t.add_row("Max sustained turn rate", f"{math.degrees(res.omega_max_sus):.2f} °/s", spd(res.v_max_sus))
            t.add_row("  load factor / bank", f"{res.n_max_sus:.2f} g / {math.degrees(math.acos(1 / res.n_max_sus)):.1f}°", "")
            t.add_row("  radius", f"{rf(res.v_max_sus**2 / (9.80665 * math.sqrt(res.n_max_sus**2 - 1))):,.0f} {lu}", "")
        else:
            t.add_row("Max sustained turn rate", "[yellow]not sustainable[/]", "")
        if res.v_level_range:
            t.add_row("Level-flight speed range (T = D)", "", f"{spd(res.v_level_range[0])} – {spd(res.v_level_range[1])}")
        t.add_row("Dive / q-limit V_D", "", spd(res.v_max))
        console.print(t)
        _print_warnings(env, quiet)
    _print_written(written, quiet)


# ---------------------------------------------------------------------------
# info
# ---------------------------------------------------------------------------
@app.command("info")
def info_cmd(
    config: Path = ConfigOpt,
    mass: Optional[str] = MassOpt,
    as_json: bool = typer.Option(False, "--json", help="Print the validated configuration as SI JSON."),
):
    """Inspect and validate an aircraft configuration."""
    ac = _load(config, mass)
    if as_json:
        console.print_json(ac.model_dump_json())
        return
    n_pos, n_neg, src_p, src_n = resolve_limits(ac)

    t = Table(title=f"{ac.name}", title_style="bold", header_style="bold cyan")
    t.add_column("Parameter")
    t.add_column("SI", justify="right")
    t.add_column("Imperial", justify="right")
    t.add_row("Category / basis", ac.category.value, ac.regulation_resolved.value.upper())
    t.add_row("Mass / weight", f"{ac.mass:,.1f} kg", f"{ac.mass / 0.45359237:,.0f} lb")
    t.add_row("Wing area", f"{ac.wing_area:,.2f} m²", f"{ac.wing_area / FT**2:,.1f} ft²")
    t.add_row("MAC", f"{ac.wing_chord:.3f} m", f"{ac.wing_chord / FT:.2f} ft")
    t.add_row("Wing loading W/S", f"{ac.wing_loading:,.1f} Pa", f"{ac.wing_loading / (LBF / FT**2):.2f} lb/ft²")
    t.add_row("Aspect ratio / e / K", f"{ac.aspect_ratio:g} / {ac.oswald_efficiency:g}", f"K = {ac.induced_drag_factor:.4f}")
    t.add_row("C_D0", f"{ac.cd0:.4f}", "")
    t.add_row("C_L,max / C_L,min", f"{ac.cl_max_clean:.2f} / {ac.cl_min_clean:.2f}", "")
    t.add_row("C_Lα", f"{ac.lift_curve_slope:.3f} /rad", f"{ac.lift_curve_slope * math.pi / 180:.4f} /deg")
    if ac.max_power_sl:
        t.add_row("Max power (SL)", f"{ac.max_power_sl / 1000:,.1f} kW", f"{ac.max_power_sl / HP:,.0f} hp")
        t.add_row("Propeller efficiency", f"{ac.prop_efficiency:.2f}", "")
    if ac.max_thrust_sl:
        t.add_row("Max thrust (SL)" + (" cap" if ac.max_power_sl else ""), f"{ac.max_thrust_sl / 1000:,.2f} kN",
                  f"{ac.max_thrust_sl / LBF:,.0f} lbf")
        if not ac.max_power_sl:
            w = ac.weight
            t.add_row("Thrust/weight (SL)", f"{ac.max_thrust_sl / w:.3f}", f"lapse σ^{ac.thrust_lapse_exponent:g}")
    t.add_row("V_C / V_D (EAS)", f"{ac.v_c:.1f} / {ac.v_d_resolved:.1f} m/s",
              f"{ac.v_c / KT:.0f} / {ac.v_d_resolved / KT:.0f} KEAS")
    if ac.v_b:
        t.add_row("V_B (EAS)", f"{ac.v_b:.1f} m/s", f"{ac.v_b / KT:.0f} KEAS")
    t.add_row("n limit +", f"{n_pos:+.2f} g", src_p)
    t.add_row("n limit −", f"{n_neg:+.2f} g", src_n)
    t.add_row("Ultimate factor", f"{ac.ultimate_factor:g}", "")
    console.print(t)

    res = compute_vn(ac, 0.0, n_points=200)
    sp = res.speeds
    console.print(_speed_table("Sea-level characteristic speeds", [
        ("V_S1", "Stall speed (1 g)", sp["vs1"]), ("V_A", "Maneuvering speed", sp["va"]),
        ("V_C", "Design cruise", sp["vc"]), ("V_D", "Design dive", sp["vd"])], res.sigma))
    _print_warnings(build_vn_envelope(res), False)


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------
@app.command("schema")
def schema_cmd(
    which: str = typer.Argument("envelope", help="'envelope' (output contract) or 'aircraft' (input config)."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Write to file instead of stdout."),
):
    """Print the JSON Schema of the exported data contract or the aircraft config."""
    models = {"envelope": EnvelopeData, "aircraft": Aircraft}
    if which not in models:
        err.print(f"[red]unknown schema {which!r}; choose from {sorted(models)}[/]")
        raise typer.Exit(code=2)
    text = json.dumps(models[which].model_json_schema(), indent=2)
    if output:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]✔[/] schema → [bold]{output}[/]")
    else:
        console.print_json(text)


if __name__ == "__main__":  # pragma: no cover
    app()
