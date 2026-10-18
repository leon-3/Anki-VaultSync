#!/usr/bin/env python3
"""Packaging & Verification Script for Anki-VaultSync.

Automates:
1. AST forbidden-import check across all vendored markdown2anki modules.
2. Vendoring decoupled markdown2anki engine, templates, and pure-Python dependencies (markdown, tomlkit).
3. Preserving third-party licenses for all vendored components (BSD-3-Clause, MIT).
4. Packaging clean dist/anki-vaultsync.ankiaddon zip archive.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import importlib.util
import os
import shutil
import sys
import zipfile
from pathlib import Path

FORBIDDEN_MODULES = {"genanki", "rich", "questionary", "typer"}

M2A_ALLOWED_FILES = [
    "__init__.py",
    "build.py",
    "config.py",
    "constants.py",
    "diagnostics.py",
    "flags.py",
    "formatting.py",
    "ids.py",
    "models.py",
    "occlusions.py",
    "parser.py",
    "render.py",
    "vault.py",
]


class ForbiddenImportChecker(ast.NodeVisitor):
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_pkg = alias.name.split(".")[0]
            if top_pkg in FORBIDDEN_MODULES:
                self.violations.append(
                    f"{self.file_path}:{node.lineno} forbidden import '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top_pkg = node.module.split(".")[0]
            if top_pkg in FORBIDDEN_MODULES:
                self.violations.append(
                    f"{self.file_path}:{node.lineno} forbidden from-import '{node.module}'"
                )
        self.generic_visit(node)


def check_ast_imports(target_dir: Path) -> list[str]:
    violations: list[str] = []
    for py_file in target_dir.glob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            checker = ForbiddenImportChecker(py_file)
            checker.visit(tree)
            violations.extend(checker.violations)
        except Exception as exc:
            violations.append(f"{py_file}: failed to parse AST: {exc}")
    return violations


def resolve_source_repo(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path.resolve()
    env_path = os.environ.get("M2A_REPO")
    if env_path:
        return Path(env_path).resolve()

    # Default heuristic: check sibling directory ../Markdown2Anki
    candidate = Path(__file__).resolve().parent.parent.parent / "Markdown2Anki"
    if candidate.is_dir() and (candidate / "markdown2anki").is_dir():
        return candidate.resolve()
    raise FileNotFoundError(
        "Could not locate Markdown2Anki repository. Please pass --source-repo <path> or set M2A_REPO."
    )


def resolve_package_dir(package_name: str) -> Path:
    spec = importlib.util.find_spec(package_name)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(f"Package '{package_name}' not found in current Python environment.")
    return Path(list(spec.submodule_search_locations)[0]).resolve()


def resolve_package_license(package_name: str) -> Path | None:
    """Locates the installed license file for a package from dist-info metadata."""
    try:
        dist = importlib.metadata.distribution(package_name)
        if dist.files:
            for f in dist.files:
                name_lower = f.name.lower()
                if "license" in name_lower or "licence" in name_lower or "copying" in name_lower:
                    path = Path(dist.locate_file(f))
                    if path.is_file():
                        return path
    except Exception:
        pass
    return None


def clean_pycache(target_dir: Path) -> None:
    for cache_dir in target_dir.rglob("__pycache__"):
        if cache_dir.is_dir():
            shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in target_dir.rglob("*.py[cod]"):
        try:
            pyc.unlink(missing_ok=True)
        except OSError:
            pass


def vendor_sync(repo_root: Path, vendor_dir: Path) -> None:
    m2a_source = repo_root / "markdown2anki"
    if not m2a_source.is_dir():
        raise FileNotFoundError(f"Missing {m2a_source}")

    vendor_m2a = vendor_dir / "markdown2anki"
    if vendor_m2a.exists():
        shutil.rmtree(vendor_m2a)
    vendor_m2a.mkdir(parents=True, exist_ok=True)

    # 1. Copy allowed Python modules
    for filename in M2A_ALLOWED_FILES:
        src_file = m2a_source / filename
        if not src_file.is_file():
            raise FileNotFoundError(f"Required engine file missing: {src_file}")
        dest_file = vendor_m2a / filename
        shutil.copy2(src_file, dest_file)

    # Copy Markdown2Anki LICENSE if present
    m2a_license = repo_root / "LICENSE"
    if m2a_license.is_file():
        shutil.copy2(m2a_license, vendor_m2a / "LICENSE")

    # Sanitize __init__.py so unvendored export package is lazily resolved
    dest_init = vendor_m2a / "__init__.py"
    if dest_init.is_file():
        init_code = dest_init.read_text(encoding="utf-8")
        # Remove eager export.ankiconnect import if present
        init_code = init_code.replace(
            "from .export.ankiconnect import AnkiConnect, AnkiConnectError, AnkiExport, export_ankiconnect\n",
            "# .export is omitted in vendored runtime\n",
        )
        # Ensure lazy __getattr__ gracefully handles optional export modules
        lazy_hook = '''def __getattr__(name: str):
    if name in ("export_apkg", "ApkgExport"):
        try:
            from .export import apkg
            return getattr(apkg, name)
        except ImportError:
            pass
    if name in ("AnkiConnect", "AnkiConnectError", "AnkiExport", "export_ankiconnect"):
        try:
            from .export import ankiconnect
            return getattr(ankiconnect, name)
        except ImportError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")'''
        if "def __getattr__" in init_code:
            lines = init_code.splitlines()
            fn_start = -1
            for idx, line in enumerate(lines):
                if line.startswith("def __getattr__"):
                    fn_start = idx
                    break
            if fn_start != -1:
                # Find end of function
                fn_end = len(lines)
                for idx in range(fn_start + 1, len(lines)):
                    if lines[idx].startswith("def ") or lines[idx].startswith("__all__"):
                        fn_end = idx
                        break
                lines[fn_start:fn_end] = [lazy_hook]
                init_code = "\n".join(lines) + "\n"
        dest_init.write_text(init_code, encoding="utf-8")

    # 2. Copy templates/ directory
    src_templates = m2a_source / "templates"
    if not src_templates.is_dir():
        raise FileNotFoundError(f"Templates directory missing: {src_templates}")
    shutil.copytree(src_templates, vendor_m2a / "templates")

    # 3. Copy pure-Python dependencies: markdown, tomlkit (with respective licenses)
    for pkg_name in ("markdown", "tomlkit"):
        pkg_source = resolve_package_dir(pkg_name)
        pkg_dest = vendor_dir / pkg_name
        if pkg_dest.exists():
            shutil.rmtree(pkg_dest)
        shutil.copytree(pkg_source, pkg_dest)

        lic_file = resolve_package_license(pkg_name)
        if lic_file and lic_file.is_file():
            shutil.copy2(lic_file, pkg_dest / lic_file.name)
            print(f"[+] Included license for {pkg_name}: {lic_file.name}")

    clean_pycache(vendor_dir)


def package_ankiaddon(addon_root: Path, dist_dir: Path) -> Path:
    dist_dir.mkdir(parents=True, exist_ok=True)
    out_file = dist_dir / "anki-vaultsync.ankiaddon"

    included_entries = [
        "__init__.py",
        "manifest.json",
        "config.json",
        "config.md",
        "LICENSE",
        "THIRD_PARTY_LICENSES.md",
        "README.md",
        "src",
        "vendor",
    ]

    with zipfile.ZipFile(out_file, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry_name in included_entries:
            entry_path = addon_root / entry_name
            if not entry_path.exists():
                continue
            if entry_path.is_file():
                zf.write(entry_path, entry_name)
            elif entry_path.is_dir():
                for root, dirs, files in os.walk(entry_path):
                    dirs[:] = [d for d in dirs if d != "__pycache__" and not d.startswith(".")]
                    for file in files:
                        if file.endswith((".pyc", ".pyo")) or file.startswith("."):
                            continue
                        full_path = Path(root) / file
                        rel_path = full_path.relative_to(addon_root)
                        zf.write(full_path, str(rel_path))

    return out_file


def main() -> int:
    parser = argparse.ArgumentParser(description="Vendor and package Anki-VaultSync.")
    parser.add_argument("--source-repo", type=Path, default=None, help="Path to Markdown2Anki repo.")
    parser.add_argument("--package", action="store_true", help="Create .ankiaddon zip in dist/.")
    parser.add_argument("--check-only", action="store_true", help="Only verify AST without re-copying.")
    args = parser.parse_args()

    addon_root = Path(__file__).resolve().parent.parent
    vendor_dir = addon_root / "vendor"

    if not args.check_only:
        source_repo = resolve_source_repo(args.source_repo)
        print(f"[*] Vendoring from {source_repo} -> {vendor_dir}")
        vendor_sync(source_repo, vendor_dir)
        print("[+] Vendor sync complete.")

    vendor_m2a = vendor_dir / "markdown2anki"
    print(f"[*] Running AST forbidden-import check on {vendor_m2a}...")
    violations = check_ast_imports(vendor_m2a)
    if violations:
        print("[-] AST check FAILED! Forbidden imports detected:")
        for v in violations:
            print(f"    {v}")
        return 1
    print("[+] AST check PASSED: zero forbidden imports.")

    if args.package:
        dist_dir = addon_root / "dist"
        archive_path = package_ankiaddon(addon_root, dist_dir)
        print(f"[+] Packaged archive created: {archive_path} ({archive_path.stat().st_size} bytes)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
