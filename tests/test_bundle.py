from __future__ import annotations

import zipfile
from pathlib import Path
from scripts.bundle import check_ast_imports, package_ankiaddon


def test_ast_checker_detects_forbidden_import(tmp_path: Path):
    bad_file = tmp_path / "bad.py"
    bad_file.write_text("import rich\nprint('hello')", encoding="utf-8")

    violations = check_ast_imports(tmp_path)
    assert len(violations) == 1
    assert "forbidden import 'rich'" in violations[0]


def test_ast_checker_passes_on_clean_file(tmp_path: Path):
    clean_file = tmp_path / "clean.py"
    clean_file.write_text("import json\nimport markdown\nprint('clean')", encoding="utf-8")

    violations = check_ast_imports(tmp_path)
    assert len(violations) == 0


def test_package_ankiaddon(tmp_path: Path):
    addon_root = Path(__file__).resolve().parent.parent
    dist_dir = tmp_path / "dist"

    archive_path = package_ankiaddon(addon_root, dist_dir)
    assert archive_path.is_file()
    assert archive_path.name == "anki-vaultsync.ankiaddon"

    # Verify contents of zip
    with zipfile.ZipFile(archive_path, "r") as zf:
        namelist = zf.namelist()
        assert "manifest.json" in namelist
        assert "config.json" in namelist
        assert "src/contracts.py" in namelist
        assert "vendor/markdown2anki/__init__.py" in namelist
        # Verify no cache or pyc
        assert not any(n.endswith(".pyc") for n in namelist)
        assert not any("__pycache__" in n for n in namelist)
        # Verify no test or spec files
        assert not any(n.startswith("tests/") for n in namelist)
        assert not any("SPEC" in n for n in namelist)


def test_all_python_files_compile():
    addon_root = Path(__file__).resolve().parent.parent
    py_files = list((addon_root / "src").rglob("*.py")) + [addon_root / "__init__.py"]
    for py_file in py_files:
        code = py_file.read_text(encoding="utf-8")
        # compile() will catch SyntaxError, including out-of-order __future__ imports
        compiled = compile(code, str(py_file), "exec")
        assert compiled is not None
