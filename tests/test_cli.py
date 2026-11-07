"""End-to-end CLI tests via Typer's CliRunner."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from flight_envelope.cli.main import app
from flight_envelope.models.outputs import EnvelopeData

from .conftest import CESSNA_YAML, F16_YAML, TURBOPROP_YAML

runner = CliRunner()


def run(*args):
    return runner.invoke(app, [str(a) for a in args])


def ok(result):
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    return result


@pytest.mark.parametrize("cfg", [CESSNA_YAML, F16_YAML, TURBOPROP_YAML])
def test_vn_table(cfg):
    out = ok(run("vn", "-c", cfg)).output
    assert "V_A" in out and "V_S1" in out


def test_turn_table():
    out = ok(run("turn", "-c", F16_YAML, "-a", "15000")).output
    assert "Corner speed" in out


def test_info():
    out = ok(run("info", "-c", CESSNA_YAML)).output
    assert "Cessna" in out


def test_info_json():
    out = ok(run("info", "-c", F16_YAML, "--json")).output
    assert json.loads(out)["name"].startswith("F-16")


@pytest.mark.parametrize("which", ["envelope", "aircraft"])
def test_schema(which):
    out = ok(run("schema", which)).output
    assert "properties" in json.loads(out)


def test_schema_unknown():
    assert run("schema", "bogus").exit_code != 0


def test_version():
    assert "flightenv" in ok(run("--version")).output


def test_vn_png_json_csv(tmp_path):
    png, js, cs = tmp_path / "vn.png", tmp_path / "vn.json", tmp_path / "vn.csv"
    ok(run("vn", "-c", CESSNA_YAML, "-o", png, "-e", js, "-e", cs, "--dpi", 60, "-q"))
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    env = EnvelopeData.from_json(js)
    assert env.envelope_type == "vn_diagram"
    assert cs.read_text().startswith("kind,id,label")


def test_turn_svg_json(tmp_path):
    svg, js = tmp_path / "turn.svg", tmp_path / "sub" / "turn.json"
    ok(run("turn", "-c", F16_YAML, "-a", "10000", "-o", svg, "-e", js, "--dpi", 60, "-q"))
    assert "<svg" in svg.read_text()
    assert EnvelopeData.from_json(js).envelope_type == "turn_performance"


def test_turn_options(tmp_path):
    js = tmp_path / "t.json"
    ok(run("turn", "-c", CESSNA_YAML, "--bank-angles", "30,60", "--radii", "200,500",
           "--ps", "-2,-5", "-u", "m/s", "-l", "ft", "-e", js, "-q"))
    env = EnvelopeData.from_json(js)
    ids = {c.id for c in env.contours or []}
    assert {"bank_30", "bank_60"} <= ids
    assert env.units["speed"] == "m/s TAS"
    assert env.units["radius"] == "ft"


def test_dark_theme_png(tmp_path):
    png = tmp_path / "dark.png"
    ok(run("vn", "-c", TURBOPROP_YAML, "-o", png, "--theme", "dark", "--dpi", 60, "-q"))
    assert png.stat().st_size > 0
    png2 = tmp_path / "dark_turn.png"
    ok(run("turn", "-c", CESSNA_YAML, "-o", png2, "-t", "dark", "--dpi", 60, "-q"))
    assert png2.stat().st_size > 0


def test_html_output(tmp_path):
    pytest.importorskip("plotly")
    html = tmp_path / "vn.html"
    ok(run("vn", "-c", CESSNA_YAML, "-o", html, "-q"))
    text = html.read_text(encoding="utf-8")
    assert "<html" in text.lower() and "plotly" in text.lower()


def test_bad_config_path(tmp_path):
    assert run("vn", "-c", tmp_path / "missing.yaml").exit_code != 0


def test_invalid_config_content(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("aircraft:\n  name: x\n  mass: '10 furlongs'\n")
    assert run("vn", "-c", bad).exit_code == 2


def test_invalid_unit_speed():
    assert run("vn", "-c", CESSNA_YAML, "--unit-speed", "furlong").exit_code != 0


def test_export_txt_rejected(tmp_path):
    r = run("vn", "-c", CESSNA_YAML, "--export-data", tmp_path / "foo.txt")
    assert r.exit_code != 0
    assert not (tmp_path / "foo.txt").exists()


def test_altitude_out_of_range():
    assert run("vn", "-c", CESSNA_YAML, "-a", "100", "--alt-unit", "m").exit_code == 0
    assert run("vn", "-c", CESSNA_YAML, "-a", "400000").exit_code != 0


def test_bad_bank_angles():
    assert run("turn", "-c", CESSNA_YAML, "--bank-angles", "95").exit_code != 0
    assert run("turn", "-c", CESSNA_YAML, "--bank-angles", "a,b").exit_code != 0


def test_mass_override_changes_vs1(tmp_path):
    base, light = tmp_path / "base.json", tmp_path / "light.json"
    ok(run("vn", "-c", CESSNA_YAML, "-e", base, "-q"))
    ok(run("vn", "-c", CESSNA_YAML, "--mass", "2000 lb", "-e", light, "-q"))
    vb = EnvelopeData.from_json(base).summary["vs1"].value
    vl = EnvelopeData.from_json(light).summary["vs1"].value
    assert vl < vb
    ratio = (2000 * 0.45359237 / 1111.0) ** 0.5
    assert vl == pytest.approx(vb * ratio, rel=1e-4)
    assert EnvelopeData.from_json(light).flight_conditions["mass_kg"] == pytest.approx(907.185, abs=1e-3)


def test_bad_mass_override():
    assert run("vn", "-c", CESSNA_YAML, "--mass", "-5 kg").exit_code == 2
