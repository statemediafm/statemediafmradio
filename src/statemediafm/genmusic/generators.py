"""User/contributor **ambient generators** loaded from config files.

The built-in generators (``Entrainment 0.1``, ``ScratchPad``) are Python render
functions. This module lets an install add *more* — a contributor drops a small
config file into a ``generators/`` directory and it becomes selectable, without
touching the package. See ``generators/README.md`` for the scaffold + template.

A generator config (TOML) has:

- ``name`` / ``description`` — how it appears.
- ``prompt`` — the composition rules / aesthetic in prose (the design spec; the
  same kind of rule base the built-ins were grown from, and a hook for a future
  prompt-driven renderer).
- ``renderer`` — *optional* ``"package.module:function"`` of a
  ``render(signal, intensity, band, fade_ms) -> str``. A spec **with** a renderer
  becomes a playable, selectable generator; a spec **without** one is a design
  spec only (listed, not registered).
- ``[params]`` — optional free-form parameters a renderer may read.
"""

from __future__ import annotations

import importlib
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .styles import register_model


@dataclass
class GeneratorSpec:
    name: str
    description: str = ""
    prompt: str = ""
    renderer: str | None = None  # "package.module:function"
    params: dict = field(default_factory=dict)

    @property
    def playable(self) -> bool:
        """True when the spec names a renderer (so it can actually make music)."""
        return bool(self.renderer)


def load_generators(directory: str | Path) -> list[GeneratorSpec]:
    """Load generator specs from every ``*.toml`` in ``directory`` (files whose
    name starts with ``_`` or is ``template`` are skipped). Missing dir → []."""
    d = Path(directory)
    if not d.is_dir():
        return []
    specs: list[GeneratorSpec] = []
    for f in sorted(d.glob("*.toml")):
        if f.name.startswith("_") or f.stem == "template":
            continue
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        specs.append(
            GeneratorSpec(
                name=data.get("name") or f.stem,
                description=data.get("description", ""),
                prompt=data.get("prompt", ""),
                renderer=data.get("renderer"),
                params=data.get("params", {}),
            )
        )
    return specs


def _resolve_renderer(path: str):
    module_path, _, func = path.partition(":")
    if not func:
        raise ValueError(f"renderer {path!r} must be 'package.module:function'")
    return getattr(importlib.import_module(module_path), func)


def register_generators(specs: list[GeneratorSpec]) -> list[str]:
    """Register the playable specs (those with a ``renderer``) into the styles
    registry so they become selectable. Returns the names registered. A bad
    renderer path is skipped with a note, not fatal."""
    import sys

    registered: list[str] = []
    for spec in specs:
        if not spec.playable:
            continue
        try:
            register_model(spec.name, _resolve_renderer(spec.renderer))
            registered.append(spec.name)
        except (ImportError, AttributeError, ValueError) as exc:
            print(f"skipping generator {spec.name!r}: {exc}", file=sys.stderr)
    return registered
