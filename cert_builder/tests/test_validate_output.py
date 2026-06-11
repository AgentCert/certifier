"""Unit tests for cert_builder/validate_output.py.

The module keeps module-level pass/fail counters; tests reset them via a
fixture so each assertion is isolated. The CLI ``main`` is exercised by
writing tiny output/groundtruth JSON files to tmp_path and monkeypatching
sys.argv.
"""

import json

import pytest

import cert_builder.validate_output as vo


@pytest.fixture(autouse=True)
def _reset_counters():
    vo.passed = 0
    vo.failed = 0
    yield
    vo.passed = 0
    vo.failed = 0


def test_sorted_keys():
    assert vo.sorted_keys({"b": 1, "a": 2}) == ["a", "b"]
    assert vo.sorted_keys([1, 2]) == []


def test_check_increments_passed():
    vo.check("ok", True)
    assert vo.passed == 1 and vo.failed == 0


def test_check_increments_failed():
    vo.check("bad", False, detail="mismatch")
    assert vo.failed == 1 and vo.passed == 0


def test_compare_keys_match():
    vo.compare_keys("x", {"a": 1, "b": 2}, {"b": 9, "a": 8})
    assert vo.passed == 1 and vo.failed == 0


def test_compare_keys_mismatch():
    vo.compare_keys("x", {"a": 1}, {"a": 1, "b": 2})
    assert vo.failed == 1


def test_block_keys_sorted():
    assert vo._block_keys({"type": "text", "body": "x"}) == ["body", "type"]


def test_compare_meta_category_count():
    out = {"agent_name": "a", "categories": [{"name": "x"}]}
    gt = {"agent_name": "b", "categories": [{"name": "y"}]}
    vo.compare_meta(out, gt)
    # meta keys match, category count match (1==1), category[0] keys match
    assert vo.failed == 0
    assert vo.passed >= 3


def test_compare_sections_block_type_sequence_mismatch():
    out = [{"id": "s", "content": [{"type": "text"}]}]
    gt = [{"id": "s", "content": [{"type": "heading"}]}]
    vo.compare_sections(out, gt)
    assert vo.failed >= 1  # block type sequence differs


def test_main_all_pass(tmp_path, monkeypatch, capsys):
    report = {
        "meta": {"agent_name": "A", "categories": [{"name": "x"}]},
        "header": {
            "scorecard": [{"dimension": "d", "value": 0.5}],
            "findings": [{"severity": "good", "text": "t"}],
        },
        "sections": [
            {"id": "s1", "number": 1, "title": "T", "content": [{"type": "text", "body": "b"}]},
        ],
        "footer": "end",
    }
    out_path = tmp_path / "out.json"
    gt_path = tmp_path / "gt.json"
    out_path.write_text(json.dumps(report))
    gt_path.write_text(json.dumps(report))

    monkeypatch.setattr("sys.argv", ["validate_output.py", str(out_path), str(gt_path)])
    rc = vo.main()
    assert rc == 0
    assert "ALL STRUCTURAL CHECKS PASSED" in capsys.readouterr().out


def test_main_detects_mismatch(tmp_path, monkeypatch):
    base = {
        "meta": {"agent_name": "A", "categories": [{"name": "x"}]},
        "header": {"scorecard": [{"dimension": "d", "value": 0.5}],
                   "findings": [{"severity": "good", "text": "t"}]},
        "sections": [{"id": "s1", "number": 1, "title": "T",
                      "content": [{"type": "text", "body": "b"}]}],
        "footer": "end",
    }
    out = json.loads(json.dumps(base))
    out["sections"][0]["content"][0]["type"] = "heading"  # type mismatch
    out_path = tmp_path / "out.json"
    gt_path = tmp_path / "gt.json"
    out_path.write_text(json.dumps(out))
    gt_path.write_text(json.dumps(base))

    monkeypatch.setattr("sys.argv", ["validate_output.py", str(out_path), str(gt_path)])
    rc = vo.main()
    assert rc == 1
