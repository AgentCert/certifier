"""Unit tests for cert_builder/scripts/computation/card_builder.py."""

import json

from cert_builder.scripts.computation import card_builder as cb
from cert_builder.tests._fixtures import make_meta


def test_identity_card_items():
    card = cb._build_identity_card(make_meta())
    assert card["title"] == "Agent Identity"
    items = {i["label"]: i["value"] for i in card["items"]}
    assert items["Agent Name"] == "TestAgent"
    assert items["Agent ID"] == "agent-007"
    assert items["Certification Run"] == "run-42"
    assert items["Certification Date"] == "2026-01-01"


def test_identity_card_fallback_dash():
    card = cb._build_identity_card({})
    items = {i["label"]: i["value"] for i in card["items"]}
    assert items["Agent Name"] == "—"
    assert items["Certification Run"] == "—"


def test_scope_card_items():
    card = cb._build_scope_card(make_meta())
    items = {i["label"]: i["value"] for i in card["items"]}
    assert items["Fault Categories"] == 2
    assert items["Faults Tested"] == 4
    assert items["Total Runs"] == 20
    assert items["Successful Runs"] == 18
    assert items["Failed Runs"] == 2


def test_categories_card():
    card = cb._build_categories_card(make_meta())
    assert card["title"] == "Fault Categories Tested"
    assert card["items"][0] == {
        "label": "Application Fault",
        "value": "container-kill, pod-delete (10 runs)",
    }


def test_categories_card_handles_missing_fields():
    card = cb._build_categories_card({"categories_summary": [{}]})
    assert card["items"][0]["label"] == "Unknown Fault"
    assert card["items"][0]["value"] == "unknown (0 runs)"


def test_build_all_cards_validates():
    out = cb.build_all_cards(make_meta())
    assert set(out["cards"]) == {"identity_card", "scope_card", "categories_card"}


def test_build_from_file(tmp_path):
    p = tmp_path / "phase1.json"
    p.write_text(json.dumps({"meta": make_meta()}))
    out = cb.build_from_file(p)
    assert "identity_card" in out["cards"]
