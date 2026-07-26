"""Tests for config-driven ambient generators (scaffold + registration)."""

from __future__ import annotations

from statemediafm.genmusic.generators import load_generators, register_generators
from statemediafm.genmusic.styles import AMBIENT_MODELS, STYLES
from statemediafm.roster import genmusic_settings


def test_genmusic_settings_defaults_and_parsing():
    assert genmusic_settings({}) == {
        "generator": "Entrainment 0.1",
        "selector": False,
        "generators_dir": None,
    }
    cfg = {"genmusic": {"generator": "ScratchPad", "selector": True, "generators": "gens"}}
    assert genmusic_settings(cfg) == {
        "generator": "ScratchPad",
        "selector": True,
        "generators_dir": "gens",
    }


def test_load_generators_reads_specs_and_skips_template(tmp_path):
    (tmp_path / "beat.toml").write_text(
        'name = "Cfg Beat"\ndescription = "d"\n'
        'renderer = "statemediafm.genmusic.styles.lofi:render"\n',
        encoding="utf-8",
    )
    (tmp_path / "spec.toml").write_text('name = "Spec Only"\nprompt = "rules"\n', encoding="utf-8")
    (tmp_path / "template.toml").write_text('name = "skip me"\n', encoding="utf-8")
    (tmp_path / "_hidden.toml").write_text('name = "also skip"\n', encoding="utf-8")

    specs = load_generators(tmp_path)
    by_name = {s.name: s for s in specs}
    assert set(by_name) == {"Cfg Beat", "Spec Only"}  # template/_hidden skipped
    assert by_name["Cfg Beat"].playable  # has a renderer
    assert not by_name["Spec Only"].playable  # prompt only


def test_load_generators_missing_dir_is_empty(tmp_path):
    assert load_generators(tmp_path / "nope") == []


def test_register_generators_registers_only_playable(tmp_path):
    (tmp_path / "beat.toml").write_text(
        'name = "Cfg Beat X"\nrenderer = "statemediafm.genmusic.styles.lofi:render"\n', encoding="utf-8"
    )
    (tmp_path / "spec.toml").write_text('name = "Spec Only X"\nprompt = "r"\n', encoding="utf-8")
    try:
        registered = register_generators(load_generators(tmp_path))
        assert registered == ["Cfg Beat X"]  # only the one with a renderer
        assert "Cfg Beat X" in STYLES and "Cfg Beat X" in AMBIENT_MODELS
        assert "Spec Only X" not in STYLES  # design spec: loaded, not registered
    finally:  # keep the global registry clean for other tests
        STYLES.pop("Cfg Beat X", None)
        if "Cfg Beat X" in AMBIENT_MODELS:
            AMBIENT_MODELS.remove("Cfg Beat X")


def test_register_generators_skips_bad_renderer(tmp_path):
    (tmp_path / "bad.toml").write_text(
        'name = "Broken"\nrenderer = "no.such.module:render"\n', encoding="utf-8"
    )
    assert register_generators(load_generators(tmp_path)) == []  # skipped, not fatal
    assert "Broken" not in STYLES
