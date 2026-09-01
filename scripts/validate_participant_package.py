#!/usr/bin/env python3
"""Offline release gate for the exported participant repository."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import sys
import urllib.parse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_NAME = "PUBLIC_PACKAGE_MANIFEST.json"
PACKAGE_NAME = "alliander-foundry-workshop-participant"
IGNORED_RUNTIME_PARTS = {
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "node_modules",
}
FORBIDDEN_PATH_PARTS = {
    ".idea",
    ".vscode",
    "admin",
    "evidence",
    "facilitator",
    "infrastructure",
    "internal",
    "proof",
    "solution",
}
FORBIDDEN_SUFFIXES = {
    ".bak",
    ".cast",
    ".env",
    ".key",
    ".log",
    ".mov",
    ".mp4",
    ".p12",
    ".pem",
    ".pfx",
    ".pyc",
    ".tsbuildinfo",
    ".webm",
}
MAX_FILE_BYTES = 5 * 1024 * 1024
UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
SECRET_PATTERNS = (
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("GitHub token", re.compile(r"\b(?:github_pat_|gh[pousr]_)\w{20,}\b")),
    ("OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("Azure storage account key", re.compile(r"(?i)\bAccountKey\s*=\s*[^;<\s]{16,}")),
    ("SAS signature", re.compile(r"(?i)(?:[?&;]|^)sig=[A-Za-z0-9%/+_-]{16,}")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)
NOTEBOOKS = (
    *sorted((ROOT / "labs" / "azure-ai-agents").glob("*.ipynb")),
    *sorted((ROOT / "labs" / "observability-and-evaluation").glob("*.ipynb")),
    ROOT / "app" / "participant" / "1-build-ai-components.ipynb",
)
REQUIRED_PATHS = (
    ROOT / ".env.example",
    ROOT / ".gitattributes",
    ROOT / "NOTICE.md",
    ROOT / "README.md",
    ROOT / "app" / "PLAYBOOK.md",
    ROOT / "app" / "backend" / "agent_names.py",
    ROOT / "app" / "backend" / "main.py",
    ROOT / "app" / "data" / "vwi_catalog.json",
    ROOT / "app" / "frontend" / "package-lock.json",
    ROOT / "requirements.lock",
    ROOT / "setup" / "Test-WorkshopPrerequisites.ps1",
    ROOT / "setup" / "validate_participant_resources.py",
)
FORBIDDEN_PATHS = (
    ROOT / "app" / "backend" / "agents",
    ROOT / "app" / "docs",
    ROOT / "app" / "scripts",
    ROOT / "docs" / "FACILITATOR_RUNBOOK.md",
    ROOT / "docs" / "INFRASTRUCTURE_RESEARCH.md",
    ROOT / "docs" / "OWNER_CHECKLIST.md",
    ROOT / "setup" / "Test-WorkshopDeployment.ps1",
    ROOT / "setup" / "access-manifest.example.json",
    ROOT / "setup" / "deploy.ps1",
)
MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PINNED_REQUIREMENT_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)(?:\[[A-Za-z0-9_,.-]+\])?"
    r"==(?P<version>[^\s;]+)(?:\s*;\s*.+)?$"
)


def _is_runtime_path(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    return any(part.casefold() in IGNORED_RUNTIME_PARTS for part in relative.parts)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_errors(path: Path, relative: Path) -> list[str]:
    data = path.read_bytes()
    if b"\x00" in data:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []

    errors = [
        f"{relative}: likely {label} detected"
        for label, pattern in SECRET_PATTERNS
        if pattern.search(text)
    ]
    for match in UUID_RE.finditer(text):
        compact = match.group(0).replace("-", "").casefold()
        if set(compact) != {"0"}:
            errors.append(
                f"{relative}: non-placeholder UUID requires publication review"
            )
            break
    return errors


def validate_repository_integrity() -> list[str]:
    errors: list[str] = []
    files: dict[str, Path] = {}

    for path in sorted(ROOT.rglob("*")):
        if _is_runtime_path(path):
            continue
        relative = path.relative_to(ROOT)
        folded_parts = {part.casefold() for part in relative.parts}
        forbidden_hits = sorted(folded_parts & FORBIDDEN_PATH_PARTS)
        if forbidden_hits:
            errors.append(
                f"{relative}: forbidden path component(s): {', '.join(forbidden_hits)}"
            )
        if path.is_symlink():
            errors.append(f"{relative}: symlinks are forbidden")
            continue
        if path.is_dir():
            continue
        if not path.is_file():
            errors.append(f"{relative}: unsupported filesystem object")
            continue

        relative_text = relative.as_posix()
        files[relative_text] = path
        name = path.name.casefold()
        if (name == ".env" or name.startswith(".env.")) and name != ".env.example":
            errors.append(f"{relative}: populated or local environment file is forbidden")
        for suffix in FORBIDDEN_SUFFIXES:
            if name.endswith(suffix) and name != ".env.example":
                errors.append(f"{relative}: forbidden file suffix {suffix}")
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{relative}: file exceeds {MAX_FILE_BYTES} bytes")
        errors.extend(_content_errors(path, relative))

    manifest_path = ROOT / MANIFEST_NAME
    if not manifest_path.is_file():
        errors.append(f"missing package manifest: {MANIFEST_NAME}")
        return errors
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{MANIFEST_NAME}: invalid JSON: {exc}")
        return errors

    if manifest.get("schema_version") != 1:
        errors.append(f"{MANIFEST_NAME}: schema_version must be 1")
    if manifest.get("package_name") != PACKAGE_NAME:
        errors.append(f"{MANIFEST_NAME}: package_name must be {PACKAGE_NAME!r}")
    recorded = manifest.get("files")
    if not isinstance(recorded, dict):
        errors.append(f"{MANIFEST_NAME}: files must be an object")
        return errors

    actual_paths = set(files) - {MANIFEST_NAME}
    recorded_paths = set(recorded)
    for missing in sorted(recorded_paths - actual_paths):
        errors.append(f"manifested file is missing: {missing}")
    for unexpected in sorted(actual_paths - recorded_paths):
        errors.append(f"unexpected file is not in the package manifest: {unexpected}")
    for relative, expected_hash in sorted(recorded.items()):
        path = files.get(relative)
        if path is None:
            continue
        if not isinstance(expected_hash, str) or not re.fullmatch(
            r"[0-9a-f]{64}", expected_hash
        ):
            errors.append(f"{MANIFEST_NAME}: invalid SHA-256 for {relative}")
            continue
        if _sha256(path) != expected_hash:
            errors.append(f"manifest hash mismatch: {relative}")
    return errors


def _cell_source(cell: dict) -> str:
    source = cell.get("source", "")
    return "".join(source) if isinstance(source, list) else str(source)


def _compile_notebook(path: Path, notebook: dict) -> list[str]:
    errors: list[str] = []
    for index, cell in enumerate(notebook.get("cells", []), start=1):
        if cell.get("cell_type") != "code":
            continue
        lines = _cell_source(cell).splitlines()
        if lines and lines[0].lstrip().startswith("%%"):
            continue
        source = "\n".join(
            line for line in lines if not line.lstrip().startswith(("%", "!"))
        )
        try:
            compile(
                source,
                f"{path}:cell-{index}",
                "exec",
                flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
            )
        except SyntaxError as exc:
            errors.append(f"{path.relative_to(ROOT)}: code cell {index}: {exc}")
    return errors


def validate_notebooks() -> list[str]:
    errors: list[str] = []
    if len(NOTEBOOKS) != 15:
        errors.append(f"expected 15 participant notebooks, found {len(NOTEBOOKS)}")
    for path in NOTEBOOKS:
        if not path.is_file():
            errors.append(f"missing notebook: {path.relative_to(ROOT)}")
            continue
        try:
            notebook = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.relative_to(ROOT)}: invalid notebook JSON: {exc}")
            continue
        if notebook.get("nbformat") != 4:
            errors.append(f"{path.relative_to(ROOT)}: expected nbformat 4")
        for index, cell in enumerate(notebook.get("cells", []), start=1):
            if cell.get("cell_type") != "code":
                continue
            if cell.get("outputs"):
                errors.append(
                    f"{path.relative_to(ROOT)}: code cell {index} has saved output"
                )
            if cell.get("execution_count") is not None:
                errors.append(
                    f"{path.relative_to(ROOT)}: code cell {index} has execution count"
                )
        errors.extend(_compile_notebook(path, notebook))

    starter = ROOT / "app" / "participant" / "1-build-ai-components.ipynb"
    if starter.is_file():
        text = starter.read_text(encoding="utf-8")
        for task in range(1, 5):
            if f"TODO {task}" not in text:
                errors.append(f"participant notebook is missing TODO {task}")
    return errors


def validate_boundary() -> list[str]:
    errors = [
        f"missing required participant file: {path.relative_to(ROOT)}"
        for path in REQUIRED_PATHS
        if not path.is_file()
    ]
    errors.extend(
        f"forbidden internal/admin path is present: {path.relative_to(ROOT)}"
        for path in FORBIDDEN_PATHS
        if path.exists()
    )
    return errors


def validate_dependency_lock() -> list[str]:
    errors: list[str] = []

    def pins(path: Path) -> dict[str, str]:
        result: dict[str, str] = {}
        for number, raw_line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if raw_line[0].isspace():
                continue
            match = PINNED_REQUIREMENT_RE.fullmatch(raw_line.strip())
            if match is None:
                errors.append(
                    f"{path.name}:{number}: dependency is not exactly pinned"
                )
                continue
            name = re.sub(r"[-_.]+", "-", match.group("name")).casefold()
            if name in result:
                errors.append(f"{path.name}:{number}: duplicate dependency {name}")
                continue
            result[name] = match.group("version")
        return result

    direct = pins(ROOT / "requirements.txt")
    locked = pins(ROOT / "requirements.lock")
    if len(locked) <= len(direct):
        errors.append("requirements.lock does not contain a resolved dependency graph")
    for name, version in sorted(direct.items()):
        if name not in locked:
            errors.append(f"requirements.lock is missing direct dependency {name}")
        elif locked[name] != version:
            errors.append(
                f"requirements.lock pin for {name} does not match requirements.txt"
            )
    return errors


def validate_markdown_links() -> list[str]:
    errors: list[str] = []
    for path in sorted(ROOT.rglob("*.md")):
        if _is_runtime_path(path):
            continue
        relative = path.relative_to(ROOT)
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
        text = re.sub(r"`[^`]*`", "", text)
        for raw_target in MARKDOWN_LINK_RE.findall(text):
            target = raw_target.strip().strip("<>").split(maxsplit=1)[0]
            if not target or target.startswith(("#", "mailto:")) or "://" in target:
                continue
            target = urllib.parse.unquote(target.split("#", 1)[0].split("?", 1)[0])
            if not target:
                continue
            candidate = (path.parent / target).resolve()
            try:
                candidate.relative_to(ROOT)
            except ValueError:
                errors.append(f"{relative}: local link escapes package: {raw_target}")
                continue
            if not candidate.exists():
                errors.append(f"{relative}: broken local link: {raw_target}")
    return errors


def main() -> int:
    errors = [
        *validate_repository_integrity(),
        *validate_boundary(),
        *validate_dependency_lock(),
        *validate_notebooks(),
        *validate_markdown_links(),
    ]
    if errors:
        print("Participant package validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(
        "Participant package validation passed: manifest, boundary, secrets, "
        "notebooks, code and links."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
