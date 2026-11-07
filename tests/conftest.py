"""Shared fixtures: the three example aircraft shipped in ``examples/``."""

from __future__ import annotations

from pathlib import Path

import pytest

from flight_envelope.models.aircraft import Aircraft, load_aircraft

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
CESSNA_YAML = EXAMPLES / "cessna172.yaml"
F16_YAML = EXAMPLES / "f16.yaml"
TURBOPROP_YAML = EXAMPLES / "regional_turboprop.yaml"


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def cessna() -> Aircraft:
    return load_aircraft(CESSNA_YAML)


@pytest.fixture(scope="session")
def f16() -> Aircraft:
    return load_aircraft(F16_YAML)


@pytest.fixture(scope="session")
def turboprop() -> Aircraft:
    return load_aircraft(TURBOPROP_YAML)


@pytest.fixture(scope="session", params=["cessna", "f16", "turboprop"])
def any_aircraft(request) -> Aircraft:
    return request.getfixturevalue(request.param)
