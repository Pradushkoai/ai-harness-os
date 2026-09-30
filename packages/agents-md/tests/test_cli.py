"""CLI tests: init / validate against tmp projects (hermetic)."""

from __future__ import annotations

import pytest

from agents_md import cli


class TestInit:
    def test_init_writes_file(self, python_project, capsys):
        code = cli.main(["init", str(python_project)])
        out = capsys.readouterr().out

        assert code == 0
        assert "Сгенерировано" in out
        assert (python_project / "AGENTS.md").is_file()

    def test_init_prints_detection_summary(self, python_project, capsys):
        cli.main(["init", str(python_project)])
        out = capsys.readouterr().out
        assert "kind=python" in out
        assert "tests=pytest" in out

    def test_init_stdout_does_not_write(self, python_project, capsys):
        code = cli.main(["init", str(python_project), "--stdout"])
        out = capsys.readouterr().out

        assert code == 0
        assert "## Architecture" in out
        assert not (python_project / "AGENTS.md").exists()

    def test_init_refuses_overwrite_without_force(self, python_project, capsys):
        cli.main(["init", str(python_project)])
        capsys.readouterr()

        code = cli.main(["init", str(python_project)])
        err = capsys.readouterr().err

        assert code == 2
        assert "--force" in err

    def test_init_force_overwrites(self, python_project, capsys):
        cli.main(["init", str(python_project)])
        (python_project / "AGENTS.md").write_text("STALE", encoding="utf-8")

        code = cli.main(["init", str(python_project), "--force"])

        assert code == 0
        assert "STALE" not in (python_project / "AGENTS.md").read_text(encoding="utf-8")

    def test_init_missing_dir_exit_two(self, tmp_path, capsys):
        code = cli.main(["init", str(tmp_path / "nope")])
        assert code == 2
        assert "не найдена" in capsys.readouterr().err

    def test_init_1c_project(self, edt_project, capsys):
        code = cli.main(["init", str(edt_project)])
        out = capsys.readouterr().out

        assert code == 0
        assert "kind=1c-edt" in out
        content = (edt_project / "AGENTS.md").read_text(encoding="utf-8")
        assert "1С:Предприятие" in content

    def test_generated_file_validates(self, python_project):
        cli.main(["init", str(python_project)])
        code = cli.main(["validate", str(python_project)])
        assert code == 0


class TestValidate:
    def test_validate_ok(self, python_project):
        cli.main(["init", str(python_project)])
        code = cli.main(["validate", str(python_project)])
        assert code == 0

    def test_validate_broken_file(self, tmp_path, capsys):
        (tmp_path / "AGENTS.md").write_text("# AGENTS.md\n\nПустой файл.\n", encoding="utf-8")
        code = cli.main(["validate", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 1
        assert "INVALID" in out

    def test_validate_missing_file(self, tmp_path, capsys):
        code = cli.main(["validate", str(tmp_path)])
        out = capsys.readouterr().out
        assert code == 1
        assert "not found" in out

    def test_validate_explicit_file(self, tmp_path, capsys):
        target = tmp_path / "OTHER.md"
        target.write_text(
            "# AGENTS.md\n\n## Project Overview\n\nОбзор проекта с описанием.\n\n"
            "## Setup\n\nУстановка по шагам здесь.\n\n## Architecture\n\nСтруктура.\n\n"
            "## Testing\n\nТесты.\n",
            encoding="utf-8",
        )
        code = cli.main(["validate", str(tmp_path), "--file", str(target)])
        assert code == 0


class TestVersion:
    def test_root_version(self, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["--version"])
        assert excinfo.value.code == 0
        assert "agents-md" in capsys.readouterr().out

    def test_subparser_versions(self, capsys):
        """--version must work through every real invocation path (bsl-check lesson)."""

        for sub in ("init", "validate"):
            with pytest.raises(SystemExit) as excinfo:
                cli.main([sub, "--version"])
            assert excinfo.value.code == 0
            assert "agents-md" in capsys.readouterr().out

    def test_console_script_reads_sys_argv(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("sys.argv", ["agents-md", "validate", str(tmp_path)])
        code = cli.main()
        assert code == 1  # no AGENTS.md in tmp_path -> invalid
        assert "not found" in capsys.readouterr().out
