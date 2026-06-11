"""Unit tests for cert_builder/scripts/computation/hardcoded_loader.py.

Reads the real YAML config (deterministic, no I/O mock needed) and
asserts the loaded shape + accessor functions.
"""

from cert_builder.scripts.computation import hardcoded_loader as hl


def test_load_all_validates_and_wraps():
    out = hl.load_all()
    assert "hardcoded" in out
    content = out["hardcoded"]
    assert set(content) >= {
        "definitions", "normalization", "statistics",
        "section_intros", "methodology_bullets"}


def test_get_definitions_returns_dict():
    defs = hl.get_definitions()
    assert isinstance(defs, dict)
    assert "ttd" in defs


def test_get_normalization():
    norm = hl.get_normalization()
    assert "score_scale" in norm


def test_get_section_intros():
    intros = hl.get_section_intros()
    assert "methodology" in intros


def test_get_methodology_bullets_nonempty_list():
    bullets = hl.get_methodology_bullets()
    assert isinstance(bullets, list)
    assert len(bullets) >= 1
    assert all(isinstance(b, str) for b in bullets)
