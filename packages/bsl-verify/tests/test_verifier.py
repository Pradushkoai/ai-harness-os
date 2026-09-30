"""Verifier tests: staging, path mapping, module text, policy verdicts."""

from __future__ import annotations

import json

import pytest

from bsl_verify import verifier as verifier_mod
from bsl_verify.policy import VerifyPolicy
from bsl_verify.verifier import BslVerifier, collect_bsl_files
from conftest import MODULE_BROKEN, REAL_REPORT, make_fake_run_analyze


@pytest.fixture
def verifier(monkeypatch):
    fake = make_fake_run_analyze()
    monkeypatch.setattr(verifier_mod, "run_analyze", fake)
    return BslVerifier()


def staged_report_for(staged_dir: str, filename: str = "module.bsl") -> str:
    """Build a realistic report whose file path lives in the staging dir."""

    return json.dumps({
        "date": "2026-09-30 10:00:00",
        "fileinfos": [{
            "path": f"file://{staged_dir}/{filename}",
            "diagnostics": [{
                "code": "ParseError",
                "severity": "Error",
                "message": "Ошибка разбора",
                "range": {"start": {"line": 3, "character": 0},
                          "end": {"line": 3, "character": 14}},
                "tags": [],
            }],
            "metrics": {"procedures": 1, "lines": 5},
        }],
    })


class TestVerifyModuleText:
    def test_returns_result_with_error(self, monkeypatch):
        # The fake must echo the REAL staging dir into the report paths.
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_module_text(MODULE_BROKEN)
        assert result.errors == 1
        assert result.passed is False
        assert result.files[0].diagnostics[0].code == "ParseError"
        assert result.report_date == "2026-09-30 10:00:00"

    def test_policy_can_be_relaxed(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        relaxed = BslVerifier(policy=VerifyPolicy(ignore_codes=frozenset({"ParseError"})))
        result = relaxed.verify_module_text(MODULE_BROKEN)
        assert result.passed is True
        assert result.errors == 0

    def test_summary_lines_rendered(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_module_text(MODULE_BROKEN)
        lines = result.summary_lines()
        assert any("FAILED" in lines[0] for _ in [0])
        assert any("ParseError" in line for line in lines)
        assert any("4:1" in line for line in lines)  # 0-based line 3 -> human 4

    def test_to_dict_json_safe(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        data = BslVerifier().verify_module_text(MODULE_BROKEN).to_dict()
        json.dumps(data)  # must not raise
        assert data["counts"]["errors"] == 1
        assert data["files"][0]["diagnostics"][0]["line"] == 4  # 1-based


class TestVerifyFiles:
    def test_staging_maps_paths_back(self, tmp_path, monkeypatch):
        original = tmp_path / "original_module.bsl"
        original.write_text(MODULE_BROKEN, encoding="utf-8")

        seen_src_dirs = []

        def fake_run(src_dir, **kw):
            seen_src_dirs.append(src_dir)
            return staged_report_for(src_dir, filename="original_module.bsl")

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_files([str(original)])

        assert len(seen_src_dirs) == 1
        staging = seen_src_dirs[0]
        assert staging not in str(original)  # analysis ran elsewhere...
        assert result.files[0].path == str(original)  # ...but report mapped back

    def test_basename_collision_prefixed(self, tmp_path, monkeypatch):
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "a" / "mod.bsl").write_text(MODULE_BROKEN, encoding="utf-8")
        (tmp_path / "b" / "mod.bsl").write_text(MODULE_BROKEN, encoding="utf-8")

        def fake_run(src_dir, **kw):
            return json.dumps({
                "fileinfos": [
                    {"path": f"file://{src_dir}/mod.bsl", "diagnostics": []},
                    {"path": f"file://{src_dir}/0001_mod.bsl", "diagnostics": []},
                ]
            })

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_files([
            str(tmp_path / "a" / "mod.bsl"), str(tmp_path / "b" / "mod.bsl")
        ])
        assert sorted(f.path for f in result.files) == sorted([
            str(tmp_path / "a" / "mod.bsl"), str(tmp_path / "b" / "mod.bsl"),
        ])

    def test_missing_file_raises(self, verifier):
        with pytest.raises(FileNotFoundError):
            verifier.verify_files(["/no/such/file.bsl"])

    def test_empty_list_raises(self, verifier):
        with pytest.raises(ValueError):
            verifier.verify_files([])


class TestVerifyDir:
    def test_direct_analysis_no_staging(self, tmp_path, monkeypatch):
        (tmp_path / "project").mkdir()
        (tmp_path / "project" / "m.bsl").write_text(MODULE_BROKEN, encoding="utf-8")

        def fake_run(src_dir, **kw):
            assert src_dir == str(tmp_path / "project")
            return staged_report_for(src_dir, filename="m.bsl")

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_dir(str(tmp_path / "project"))
        assert result.files[0].path == str(tmp_path / "project" / "m.bsl")


class TestPolicyFiltersAppliedEverywhere:
    def test_ignored_codes_removed_from_counts_summary_and_json(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        ignoring = BslVerifier(
            policy=VerifyPolicy(ignore_codes=frozenset({"ParseError"}))
        )
        result = ignoring.verify_module_text(MODULE_BROKEN)

        assert result.errors == 0
        assert result.total_diagnostics == 0
        # NB: "ParseError" legitimately appears in the policy description
        # ("ignore=ParseError"); what must vanish is the diagnostic line.
        assert not any("ERROR ParseError" in line for line in result.summary_lines())
        data = result.to_dict()
        assert data["files"][0]["diagnostics"] == []
        assert data["files"][0]["metrics"] is not None  # metrics preserved

    def test_only_codes_whitelist_hides_everything_else(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        strict = BslVerifier(
            policy=VerifyPolicy(only_codes=frozenset({"SomethingElse"}))
        )
        result = strict.verify_module_text(MODULE_BROKEN)
        assert result.total_diagnostics == 0
        assert result.passed is True

    def test_default_policy_keeps_everything(self, monkeypatch):
        def fake_run(src_dir, **kw):
            return staged_report_for(src_dir)

        monkeypatch.setattr(verifier_mod, "run_analyze", fake_run)
        result = BslVerifier().verify_module_text(MODULE_BROKEN)
        assert result.errors == 1  # ParseError kept and counted


class TestCollectBslFiles:
    def test_recursive_dir_expansion(self, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.bsl").write_text("x")
        (tmp_path / "sub" / "b.os").write_text("x")
        (tmp_path / "c.txt").write_text("x")
        files = collect_bsl_files([str(tmp_path)])
        assert [f.name for f in files] == ["a.bsl", "b.os"]

    def test_plain_files_kept(self, tmp_path):
        f = tmp_path / "x.bsl"
        f.write_text("x")
        assert collect_bsl_files([str(f)]) == [f]

    def test_missing_path_ignored(self):
        assert collect_bsl_files(["/no/such/path"]) == []
