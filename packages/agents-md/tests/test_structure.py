"""Unit tests for the directory scanner (hermetic, tmp trees only)."""

from __future__ import annotations

from pathlib import Path

from agents_md import structure as st
from agents_md.types import KIND_1C_EDT, KIND_1C_XML, KIND_GENERIC, KIND_PYTHON


def _make_tree(tmp_path: Path, spec: dict) -> Path:
    """spec: {"dir/sub": ["file.ext", ...], ...} relative to root."""

    root = tmp_path / "scanned"
    for rel, files in spec.items():
        directory = root / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name in files:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x\n", encoding="utf-8")
    return root


class TestScanStructure:
    def test_skip_dirs_and_hidden_excluded(self, tmp_path):
        root = _make_tree(
            tmp_path,
            {
                "": ["README.md"],
                "node_modules/pkg": ["a.js"],
                ".git": ["config"],
                ".venv": ["pyvenv.cfg"],
                "src": ["main.py"],
            },
        )
        names = [e.name for e in st.scan_structure(root, KIND_PYTHON)]
        assert names == ["src"]

    def test_file_counts_and_markers(self, tmp_path):
        root = _make_tree(
            tmp_path,
            {
                "src": ["a.py", "b.py", "c.py"],
                "docs": ["guide.md", "extra.md", "deep/inner.md"],
            },
        )
        entries = {e.name: e for e in st.scan_structure(root, KIND_PYTHON)}
        assert entries["src"].file_count == 3
        assert entries["src"].marker == "3 .py"
        assert entries["docs"].file_count == 3
        assert entries["docs"].marker == "3 .md"

    def test_bsl_marker_survives_low_counts(self, tmp_path):
        """1C: even two .bsl files are the interesting marker."""

        root = _make_tree(tmp_path, {"CommonModules": ["m.bsl", "n.bsl", "notes.txt"]})
        entries = {e.name: e for e in st.scan_structure(root, KIND_1C_EDT)}
        assert entries["CommonModules"].marker == "2 .bsl"

    def test_comments_for_1c_dirs(self, tmp_path):
        root = _make_tree(
            tmp_path,
            {"src/Catalogs": ["a.xml"], "src/Documents": ["b.xml"], "src/Unknown": ["c"]},
        )
        entries = {e.name: e for e in st.scan_structure(root / "src", KIND_1C_EDT)}
        assert entries["Catalogs"].comment == "Справочники"
        assert entries["Documents"].comment == "Документы"
        assert entries["Unknown"].comment == "метаданные конфигурации"

    def test_comments_for_common_dirs(self, tmp_path):
        root = _make_tree(tmp_path, {"tests": ["t.py"], "docs": ["d.md"], "data": ["x"]})
        entries = {e.name: e for e in st.scan_structure(root, KIND_PYTHON)}
        assert entries["tests"].comment == "тесты"
        assert entries["docs"].comment == "документация"
        assert entries["data"].comment == "см. содержимое"

    def test_unknown_dir_in_1c_xml(self, tmp_path):
        root = _make_tree(tmp_path, {"Whatever": ["a"]})
        entries = {e.name: e for e in st.scan_structure(root, KIND_1C_XML)}
        assert entries["Whatever"].comment == "метаданные конфигурации"

    def test_generic_kind_unknown_dir(self, tmp_path):
        root = _make_tree(tmp_path, {"Whatever": ["a"]})
        entries = {e.name: e for e in st.scan_structure(root, KIND_GENERIC)}
        assert entries["Whatever"].comment == "см. содержимое"

    def test_empty_root(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        assert st.scan_structure(root, KIND_PYTHON) == []

    def test_bounded_by_max_files(self, tmp_path):
        """A huge tree must not hang: scan stops at MAX_FILES per directory."""

        root = tmp_path / "flood"
        directory = root / "big"
        directory.mkdir(parents=True)
        for i in range(st.MAX_FILES + 500):
            (directory / f"f{i}.txt").write_text("x", encoding="utf-8")
        entries = st.scan_structure(root, KIND_GENERIC)
        assert len(entries) == 1
        assert entries[0].file_count <= st.MAX_FILES

    def test_render_entry(self, tmp_path):
        from agents_md.types import StructureEntry

        entry = StructureEntry(name="src", comment="исходный код", file_count=7, marker="5 .py")
        assert entry.render() == "src/  # исходный код (7 файлов, 5 .py)"
        plain = StructureEntry(name="data", comment="см. содержимое", file_count=2)
        assert plain.render() == "data/  # см. содержимое (2 файлов)"


class TestRenderTree:
    def _entries(self, count: int):
        from agents_md.types import StructureEntry

        return [
            StructureEntry(name=f"d{i}", comment="каталог", file_count=i)
            for i in range(1, count + 1)
        ]

    def test_tree_shape(self):
        tree = st.render_tree("proj", self._entries(2))
        lines = tree.splitlines()
        assert lines[0] == "proj/"
        assert lines[1] == "├── d1/  # каталог (1 файлов)"
        assert lines[2] == "└── d2/  # каталог (2 файлов)"

    def test_bounded_entries(self):
        tree = st.render_tree("proj", self._entries(20))
        lines = tree.splitlines()
        assert len(lines) == 1 + 15 + 1  # root + max_entries + hidden note
        assert lines[-1] == "    ... и ещё 5 директорий"

    def test_empty_tree(self):
        assert st.render_tree("proj", []) == "proj/"

    def test_exact_limit_no_hidden_note(self):
        tree = st.render_tree("proj", self._entries(15))
        assert "и ещё" not in tree


class TestSrcDir:
    def test_none_for_python(self, tmp_path):
        root = _make_tree(tmp_path, {"src": ["a.py"]})
        assert st.src_dir(root, KIND_PYTHON) is None

    def test_src_lowercase(self, tmp_path):
        root = _make_tree(tmp_path, {"src": ["a.bsl"]})
        assert st.src_dir(root, KIND_1C_EDT) == root / "src"

    def test_src_capitalized(self, tmp_path):
        root = _make_tree(tmp_path, {"Src": ["a.bsl"]})
        assert st.src_dir(root, KIND_1C_XML) == root / "Src"

    def test_no_src_dir(self, tmp_path):
        root = _make_tree(tmp_path, {"Configuration": ["Configuration.mdo"]})
        assert st.src_dir(root, KIND_1C_EDT) is None

    def test_prefers_src_over_capitalized(self, tmp_path):
        root = _make_tree(tmp_path, {"src": ["a.bsl"], "Src": ["b.bsl"]})
        assert st.src_dir(root, KIND_1C_EDT) == root / "src"


class TestMarker:
    def test_empty(self):
        assert st._marker({}) == ""

    def test_dominant_extension(self):
        assert st._marker({".py": 5, ".md": 2}) == "5 .py"

    def test_low_counts_fall_back_to_top(self):
        """<3 files of each ext: take the single most frequent one."""

        assert st._marker({".py": 2, ".md": 1}) == "2 .py"

    def test_tie_break_deterministic(self):
        marker = st._marker({".py": 3, ".md": 3})
        assert marker in ("3 .py", "3 .md")
