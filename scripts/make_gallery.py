"""Regenerate the README example images: python scripts/make_gallery.py"""

from pathlib import Path

from flight_envelope import OutputUnits, load_aircraft, turn_envelope, vn_envelope
from flight_envelope.plotting.turn_plot import plot_turn
from flight_envelope.plotting.vn_plot import plot_vn
from flight_envelope.units import FT

ROOT = Path(__file__).resolve().parents[1]
EX, OUT = ROOT / "examples", ROOT / "docs" / "images"
DPI = 130

JOBS = [
    # (file stem, aircraft, kind, altitude ft, theme, units)
    ("cessna172_vn", "cessna172", "vn", 0, "light", OutputUnits()),
    ("regional_turboprop_vn", "regional_turboprop", "vn", 0, "light", OutputUnits()),
    ("f16_vn_dark", "f16", "vn", 0, "dark", OutputUnits()),
    ("cessna172_turn", "cessna172", "turn", 5000, "light", OutputUnits(speed_type="tas")),
    ("f16_turn_dark", "f16", "turn", 15000, "dark", OutputUnits(speed_type="tas")),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for stem, ac_name, kind, alt_ft, theme, units in JOBS:
        ac = load_aircraft(EX / f"{ac_name}.yaml")
        if kind == "vn":
            _, env = vn_envelope(ac, alt_ft * FT, units)
            path = plot_vn(env, OUT / f"{stem}.png", theme, DPI)
        else:
            _, env = turn_envelope(ac, alt_ft * FT, units)
            path = plot_turn(env, OUT / f"{stem}.png", theme, DPI)
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
