"""Compatibility/setuptools entry point for older pip/setuptools versions.

The canonical metadata lives in pyproject.toml. This file keeps `pip wheel .`
and `pip install .` reproducible on Python/pip combinations that do not apply
PEP 621 metadata when building a wheel.
"""
from pathlib import Path
from setuptools import setup

setup(
    name="codex-relay",
    version="0.4.2",
    description="Codex multi-account handoff with session continuity, reasoning replay and cache diagnostics",
    long_description=(Path(__file__).resolve().parent / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="MIT",
    packages=["codex_relay"],
    package_data={"codex_relay": ["resources/*.lproj/*.strings"]},
    python_requires=">=3.9",
    entry_points={
        "console_scripts": [
            "codex-relay=codex_relay.cli:main",
        ],
    },
)
