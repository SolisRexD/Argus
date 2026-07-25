# CitySample Argus Integration Solidification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing CitySample Photo Mode integration installable, verifiable, restorable, and safely push the verified Argus `main` branch to `origin`.

**Architecture:** A stdlib-only host CLI owns path validation, exact source patching, backups, SHA-256 manifests, CitySample builds, rollback, and UE process orchestration. A separate UE 5.8 Python script owns the three asset operations and runs headlessly through `UnrealEditor-Cmd.exe -ExecutePythonScript`; `.uasset` files remain outside Git.

**Tech Stack:** Python 3, argparse, pathlib, hashlib, json, shutil, subprocess, pytest, Unreal Engine 5.8 Python API, UnrealBuildTool, Git.

---

## Scope and constraints

- Argus root default: `D:\Study\Code\Python\UE\cv\Argus`
- CitySample root default: `E:\UnrealProject\CitySample`
- UE 5.8 root default: `E:\UE_5.8`
- Existing pre-install backup: `E:\UnrealProject\CitySample\ArgusBackups\20260724_player_capture`
- Keep `AGENTS.md` untracked.
- Work on an isolated feature branch/worktree; do not implement directly on `main`.
- Do not use `unreal.SystemLibrary.quit_editor()`.
- Do not commit CitySample source copies or `.uasset` files into Argus.
- Do not add third-party Python dependencies.
- Do not add `--force`, GUI, automatic disk discovery, tags, or a PR.

## File map

- Create `scripts/citysample_argus_integration.py`: host CLI, source contract, manifest, backup, restore, build, UE invocation.
- Create `scripts/citysample_argus_assets.py`: UE-only asset install and verify entrypoint.
- Create `tests/test_citysample_argus_integration.py`: pure host tests for patching, manifests, adoption, rollback, and commands.
- Create `tests/test_citysample_argus_assets.py`: fake-Unreal tests for asset idempotency and verification.
- Modify `tests/test_citysample_photo_mode_capture_integration.py`: reuse the installer source verifier.
- Modify `docs/workflow.md`: document install, verify, restore, and path overrides.

### Task 1: Add the exact CitySample source contract and patcher

**Files:**
- Create: `scripts/citysample_argus_integration.py`
- Create: `tests/test_citysample_argus_integration.py`

- [ ] **Step 1: Write failing source patch tests**

Create `tests/test_citysample_argus_integration.py` with the initial tests below:

```python
from pathlib import Path

import pytest

from scripts.citysample_argus_integration import (
    IntegrationError,
    patch_source_texts,
    verify_source_texts,
)


BASE_HEADER = """\
\tUPROPERTY(EditDefaultsOnly, Category = \"Input\")
\tclass UInputAction* UseAutoFocusAction;

\tvoid DisableAutoFocusActionBinding();
};
"""

BASE_SOURCE = """\
#include \"InputMappingContext.h\"

void UPhotoModeComponent::SetUpInputs()
{
\t\tEnhancedInputComponent->BindAction(UseAutoFocusAction, ETriggerEvent::Completed, this, &ThisClass::DisableAutoFocusActionBinding);
}

void UPhotoModeComponent::DisableAutoFocusActionBinding()
{
\tDisableAutoFocus();
}
"""

BASE_BUILD = """\
\t\tif (Target.bBuildEditor == true)
\t\t{
\t\t\tPublicDependencyModuleNames.AddRange(new string[] { \"UnrealEd\" });
\t\t}
"""


def test_patch_source_texts_installs_exact_contract():
    argus_root = Path(r"D:\Portable\Argus")

    patched = patch_source_texts(
        BASE_HEADER,
        BASE_SOURCE,
        BASE_BUILD,
        argus_root,
    )

    verify_source_texts(*patched, argus_root)
    assert "UInputAction* CaptureAction" in patched[0]
    assert "void CaptureActionBinding();" in patched[0]
    assert '#include "IPythonScriptPlugin.h"' in patched[1]
    assert "BindAction(CaptureAction, ETriggerEvent::Started" in patched[1]
    assert "p=r'D:/Portable/Argus/scripts'" in patched[1]
    assert 'PrivateDependencyModuleNames.Add("PythonScriptPlugin")' in patched[2]


def test_patch_source_texts_is_idempotent():
    argus_root = Path(r"D:\Portable\Argus")
    first = patch_source_texts(BASE_HEADER, BASE_SOURCE, BASE_BUILD, argus_root)

    assert patch_source_texts(*first, argus_root) == first


def test_patch_source_texts_rejects_partial_installation():
    partial_header = BASE_HEADER.replace(
        "\tvoid DisableAutoFocusActionBinding();",
        "\tvoid DisableAutoFocusActionBinding();\n\tvoid CaptureActionBinding();",
    )

    with pytest.raises(IntegrationError, match="partial or conflicting"):
        patch_source_texts(
            partial_header,
            BASE_SOURCE,
            BASE_BUILD,
            Path(r"D:\Portable\Argus"),
        )


def test_verify_source_texts_rejects_wrong_argus_path():
    installed = patch_source_texts(
        BASE_HEADER,
        BASE_SOURCE,
        BASE_BUILD,
        Path(r"D:\Portable\Argus"),
    )

    with pytest.raises(IntegrationError, match="source contract"):
        verify_source_texts(*installed, Path(r"E:\Other\Argus"))
```

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: collection fails because `scripts.citysample_argus_integration` does not exist.

- [ ] **Step 3: Implement the minimal source contract**

Create `scripts/citysample_argus_integration.py` with these imports, constants, and functions:

```python
"""Install, verify, or restore the Argus CitySample integration."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_ARGUS_ROOT = Path(r"D:\Study\Code\Python\UE\cv\Argus")
DEFAULT_CITYSAMPLE_ROOT = Path(r"E:\UnrealProject\CitySample")
DEFAULT_UE_ROOT = Path(r"E:\UE_5.8")

HEADER_REL = Path("Source/CitySample/Camera/PhotoModeComponent.h")
SOURCE_REL = Path("Source/CitySample/Camera/PhotoModeComponent.cpp")
BUILD_REL = Path("Source/CitySample/CitySample.Build.cs")
MAPPING_REL = Path("Content/Input/PhotoMode/IM_PM_Simple_MappingContext.uasset")
BLUEPRINT_REL = Path("Content/Gameplay/Framework/BP_PhotoModeComponent.uasset")
ACTION_REL = Path("Content/Input/PhotoMode/IA_PM_ArgusCapture.uasset")

EXISTING_FILES = (HEADER_REL, SOURCE_REL, BUILD_REL, MAPPING_REL, BLUEPRINT_REL)
MANAGED_FILES = EXISTING_FILES + (ACTION_REL,)


class IntegrationError(RuntimeError):
    pass


def _argus_scripts_path(argus_root):
    return (Path(argus_root).resolve() / "scripts").as_posix().replace("'", "\\'")


def _insert_after_once(text, anchor, addition, marker, label):
    if addition in text:
        if text.count(addition) != 1:
            raise IntegrationError("{} is duplicated".format(label))
        return text
    if marker in text or text.count(anchor) != 1:
        raise IntegrationError("{} is partial or conflicting".format(label))
    return text.replace(anchor, anchor + addition, 1)


def expected_source_fragments(argus_root):
    scripts_path = _argus_scripts_path(argus_root)
    capture_property = """

\tUPROPERTY(EditDefaultsOnly, Category = \"Input\")
\tclass UInputAction* CaptureAction;"""
    capture_declaration = "\n\tvoid CaptureActionBinding();"
    python_include = """

#if WITH_EDITOR
#include \"IPythonScriptPlugin.h\"
#endif"""
    capture_binding = """

#if WITH_EDITOR
\t\tif (CaptureAction)
\t\t{
\t\t\tEnhancedInputComponent->BindAction(CaptureAction, ETriggerEvent::Started, this, &ThisClass::CaptureActionBinding);
\t\t}
#endif"""
    capture_handler = """

void UPhotoModeComponent::CaptureActionBinding()
{
#if WITH_EDITOR
\tif (State != EPhotoModeState::Active)
\t{
\t\treturn;
\t}

\tIPythonScriptPlugin* const PythonPlugin = IPythonScriptPlugin::Get();
\tif (!PythonPlugin)
\t{
\t\tUE_LOG(LogCitySamplePhotoMode, Error, TEXT(\"Argus capture failed: PythonScriptPlugin is unavailable.\"));
\t\treturn;
\t}

\tif (!PythonPlugin->IsPythonInitialized())
\t{
\t\tPythonPlugin->ForceEnablePythonAtRuntime();
\t}

\tif (!PythonPlugin->IsPythonInitialized())
\t{
\t\tUE_LOG(LogCitySamplePhotoMode, Error, TEXT(\"Argus capture failed: Python is not initialized.\"));
\t\treturn;
\t}

\tstatic const TCHAR* const Command = TEXT(
\t\t\"import sys; \"
\t\t\"p=r'{scripts_path}'; \"
\t\t\"sys.path.insert(0, p) if p not in sys.path else None; \"
\t\t\"import capture_player_view; \"
\t\t\"capture_player_view.capture_player_view()\"
\t);

\tif (!PythonPlugin->ExecPythonCommand(Command))
\t{
\t\tUE_LOG(LogCitySamplePhotoMode, Error, TEXT(\"Argus capture Python command failed.\"));
\t}
#endif
}""".format(scripts_path=scripts_path)
    build_dependency = "\n\t\t\tPrivateDependencyModuleNames.Add(\"PythonScriptPlugin\");\n"
    return {
        "header": (capture_property, capture_declaration),
        "source": (python_include, capture_binding, capture_handler),
        "build": (build_dependency,),
    }


def patch_source_texts(header, source, build, argus_root):
    fragments = expected_source_fragments(argus_root)
    header = _insert_after_once(
        header,
        '\tclass UInputAction* UseAutoFocusAction;',
        fragments["header"][0],
        "UInputAction* CaptureAction",
        "capture property",
    )
    header = _insert_after_once(
        header,
        "\tvoid DisableAutoFocusActionBinding();",
        fragments["header"][1],
        "void CaptureActionBinding();",
        "capture declaration",
    )
    source = _insert_after_once(
        source,
        '#include "InputMappingContext.h"',
        fragments["source"][0],
        'IPythonScriptPlugin.h',
        "Python include",
    )
    source = _insert_after_once(
        source,
        "\t\tEnhancedInputComponent->BindAction(UseAutoFocusAction, ETriggerEvent::Completed, this, &ThisClass::DisableAutoFocusActionBinding);",
        fragments["source"][1],
        "BindAction(CaptureAction",
        "capture binding",
    )
    source = _insert_after_once(
        source,
        "void UPhotoModeComponent::DisableAutoFocusActionBinding()\n{\n\tDisableAutoFocus();\n}",
        fragments["source"][2],
        "void UPhotoModeComponent::CaptureActionBinding()",
        "capture handler",
    )
    build = _insert_after_once(
        build,
        "\t\tif (Target.bBuildEditor == true)\n\t\t{",
        fragments["build"][0],
        'PrivateDependencyModuleNames.Add("PythonScriptPlugin")',
        "Python build dependency",
    )
    verify_source_texts(header, source, build, argus_root)
    return header, source, build


def verify_source_texts(header, source, build, argus_root):
    texts = {"header": header, "source": source, "build": build}
    for name, fragments in expected_source_fragments(argus_root).items():
        for fragment in fragments:
            if texts[name].count(fragment) != 1:
                raise IntegrationError("{} source contract is not installed".format(name))
    return True
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: `4 passed`.

- [ ] **Step 5: Commit the source contract**

```powershell
git add -- scripts/citysample_argus_integration.py tests/test_citysample_argus_integration.py
git commit -m "Add CitySample integration source contract"
```

### Task 2: Add safe file IO, backups, manifests, adoption, and restore

**Files:**
- Modify: `scripts/citysample_argus_integration.py`
- Modify: `tests/test_citysample_argus_integration.py`

- [ ] **Step 1: Add failing transaction tests**

Append tests that create only the six managed paths in a temporary CitySample tree:

```python
import codecs
import json
import shutil

from scripts.citysample_argus_integration import (
    ACTION_REL,
    BLUEPRINT_REL,
    BUILD_REL,
    EXISTING_FILES,
    HEADER_REL,
    LEGACY_BACKUP_PATHS,
    MAPPING_REL,
    SOURCE_REL,
    create_manifest,
    find_manifest,
    read_source_files,
    restore_manifest,
    sha256_file,
    write_source_files,
)


def make_citysample_tree(tmp_path):
    root = tmp_path / "CitySample"
    files = {
        HEADER_REL: BASE_HEADER.encode("utf-8"),
        SOURCE_REL: BASE_SOURCE.replace("\n", "\r\n").encode("utf-8"),
        BUILD_REL: codecs.BOM_UTF8 + BASE_BUILD.encode("utf-8"),
        MAPPING_REL: b"mapping-before",
        BLUEPRINT_REL: b"blueprint-before",
    }
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


def test_source_io_preserves_bom_and_newlines(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    header, source, build = read_source_files(citysample_root)
    patched = patch_source_texts(
        header[0], source[0], build[0], Path(r"D:\Portable\Argus")
    )

    write_source_files(citysample_root, patched, (header, source, build))

    assert b"\r\n" in (citysample_root / SOURCE_REL).read_bytes()
    assert (citysample_root / BUILD_REL).read_bytes().startswith(codecs.BOM_UTF8)


def test_create_manifest_copies_only_managed_backups(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    legacy = tmp_path / "legacy"
    (legacy / "Source").mkdir(parents=True)
    (legacy / "Content").mkdir(parents=True)
    (legacy / "UnexpectedSaves").mkdir()
    shutil.copy2(citysample_root / HEADER_REL, legacy / "Source/PhotoModeComponent.h")
    shutil.copy2(citysample_root / SOURCE_REL, legacy / "Source/PhotoModeComponent.cpp")
    shutil.copy2(citysample_root / BUILD_REL, legacy / "Source/CitySample.Build.cs")
    shutil.copy2(citysample_root / MAPPING_REL, legacy / "Content/IM_PM_Simple_MappingContext.uasset")
    shutil.copy2(citysample_root / BLUEPRINT_REL, legacy / "Content/BP_PhotoModeComponent.uasset")
    (legacy / "UnexpectedSaves/ignored.uasset").write_bytes(b"ignored")

    manifest_path = create_manifest(
        citysample_root,
        Path(r"D:\Portable\Argus"),
        Path(r"E:\UE_5.8"),
        adopt_backup=legacy,
        commit="abc123",
        stamp="20260725_010203",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["state"] == "installing"
    assert len(manifest["files"]) == 6
    assert not any("UnexpectedSaves" in row["path"] for row in manifest["files"])
    assert (manifest_path.parent / "files" / HEADER_REL).is_file()
    action = next(row for row in manifest["files"] if row["path"] == ACTION_REL.as_posix())
    assert action["created"] is True
    assert action["original_sha256"] is None


def test_find_manifest_matches_all_three_roots(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    wanted = create_manifest(
        citysample_root,
        tmp_path / "ArgusA",
        tmp_path / "UEA",
        commit="abc123",
        stamp="20260725_010203",
    )
    other = create_manifest(
        citysample_root,
        tmp_path / "ArgusB",
        tmp_path / "UEB",
        commit="abc123",
        stamp="20260725_010204",
    )
    for path in (wanted, other):
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["state"] = "installed"
        path.write_text(json.dumps(manifest), encoding="utf-8")

    assert find_manifest(
        tmp_path / "ArgusA", citysample_root, tmp_path / "UEA"
    ) == wanted


def test_restore_manifest_rejects_installed_drift(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        Path(r"D:\Portable\Argus"),
        Path(r"E:\UE_5.8"),
        commit="abc123",
        stamp="20260725_010203",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        row["installed_sha256"] = sha256_file(path) if path.exists() else None
    manifest["state"] = "installed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (citysample_root / HEADER_REL).write_text("user change", encoding="utf-8")

    with pytest.raises(IntegrationError, match="drift"):
        restore_manifest(manifest_path)


def test_restore_manifest_restores_existing_and_removes_created_file(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        Path(r"D:\Portable\Argus"),
        Path(r"E:\UE_5.8"),
        commit="abc123",
        stamp="20260725_010203",
    )
    original_header = (citysample_root / HEADER_REL).read_bytes()
    (citysample_root / HEADER_REL).write_bytes(b"installed")
    action_path = citysample_root / ACTION_REL
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_path.write_bytes(b"created")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        row["installed_sha256"] = sha256_file(path) if path.exists() else None
    manifest["state"] = "installed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restore_manifest(manifest_path)

    assert (citysample_root / HEADER_REL).read_bytes() == original_header
    assert not action_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "restored"
```

- [ ] **Step 2: Run the transaction tests and verify RED**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: imports fail for the new transaction functions.

- [ ] **Step 3: Implement formatting-preserving IO and manifests**

Add the following behavior to `scripts/citysample_argus_integration.py`:

```python
LEGACY_BACKUP_PATHS = {
    HEADER_REL: Path("Source/PhotoModeComponent.h"),
    SOURCE_REL: Path("Source/PhotoModeComponent.cpp"),
    BUILD_REL: Path("Source/CitySample.Build.cs"),
    MAPPING_REL: Path("Content/IM_PM_Simple_MappingContext.uasset"),
    BLUEPRINT_REL: Path("Content/BP_PhotoModeComponent.uasset"),
}


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path):
    raw = Path(path).read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8-sig").replace("\r\n", "\n"), bom, newline


def read_source_files(citysample_root):
    root = Path(citysample_root)
    return tuple(_read_text(root / relative) for relative in (HEADER_REL, SOURCE_REL, BUILD_REL))


def _write_text_atomic(path, text, bom, newline):
    path = Path(path)
    payload = text.replace("\n", newline).encode("utf-8")
    if bom:
        payload = codecs.BOM_UTF8 + payload
    temporary = path.with_name(path.name + ".argus.tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_source_files(citysample_root, texts, formats):
    root = Path(citysample_root)
    for relative, text, (_, bom, newline) in zip(
        (HEADER_REL, SOURCE_REL, BUILD_REL), texts, formats
    ):
        _write_text_atomic(root / relative, text, bom, newline)


def _write_json_atomic(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _backup_source(adopt_backup, relative, live_path):
    if not adopt_backup:
        return live_path
    source = Path(adopt_backup) / LEGACY_BACKUP_PATHS[relative]
    if not source.is_file():
        raise IntegrationError("adopt backup is missing {}".format(relative))
    return source


def create_manifest(
    citysample_root,
    argus_root,
    ue_root,
    adopt_backup=None,
    commit=None,
    stamp=None,
):
    citysample_root = Path(citysample_root).resolve()
    argus_root = Path(argus_root).resolve()
    ue_root = Path(ue_root).resolve()
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = citysample_root / "ArgusBackups/argus_integration" / stamp
    if backup_dir.exists():
        raise IntegrationError("backup already exists: {}".format(backup_dir))
    files_dir = backup_dir / "files"
    files_dir.mkdir(parents=True)
    rows = []
    for relative in EXISTING_FILES:
        live_path = citysample_root / relative
        source = _backup_source(adopt_backup, relative, live_path)
        if not live_path.is_file() or not source.is_file():
            raise IntegrationError("managed file is missing: {}".format(relative))
        backup_path = files_dir / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_path)
        rows.append({
            "path": relative.as_posix(),
            "backup": backup_path.relative_to(backup_dir).as_posix(),
            "created": False,
            "original_sha256": sha256_file(backup_path),
            "installed_sha256": None,
        })
    rows.append({
        "path": ACTION_REL.as_posix(),
        "backup": None,
        "created": True,
        "original_sha256": None,
        "installed_sha256": None,
    })
    manifest = {
        "schema_version": 1,
        "state": "installing",
        "argus_commit": commit or "",
        "created_at": stamp,
        "backup_dir": str(backup_dir),
        "roots": {
            "argus": str(argus_root),
            "citysample": str(citysample_root),
            "ue": str(ue_root),
        },
        "adopted": bool(adopt_backup),
        "completed_phases": ["backup"],
        "files": rows,
    }
    manifest_path = backup_dir / "manifest.json"
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def load_manifest(manifest_path):
    path = Path(manifest_path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise IntegrationError("unsupported manifest schema")
    return path, data


def _check_installed_hashes(manifest_path, manifest):
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        actual = sha256_file(path) if path.exists() else None
        if row.get("installed_sha256") != actual:
            raise IntegrationError("installed file drift: {}".format(row["path"]))


def restore_manifest(manifest_path, check_drift=True):
    manifest_path, manifest = load_manifest(manifest_path)
    if check_drift:
        _check_installed_hashes(manifest_path, manifest)
    manifest["state"] = "restoring"
    _write_json_atomic(manifest_path, manifest)
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        live_path = citysample_root / row["path"]
        if row["created"]:
            if live_path.exists():
                live_path.unlink()
            continue
        backup_path = manifest_path.parent / row["backup"]
        live_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_path, live_path)
    manifest["state"] = "restored"
    manifest["completed_phases"].append("restore")
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def find_manifest(argus_root, citysample_root, ue_root, explicit=None):
    if explicit:
        return load_manifest(explicit)[0]
    roots = {
        "argus": Path(argus_root).resolve(),
        "citysample": Path(citysample_root).resolve(),
        "ue": Path(ue_root).resolve(),
    }
    matches = []
    backup_root = roots["citysample"] / "ArgusBackups/argus_integration"
    for path in backup_root.glob("*/manifest.json"):
        _, data = load_manifest(path)
        manifest_roots = {
            name: Path(data["roots"][name]).resolve() for name in roots
        }
        if data.get("state") == "installed" and manifest_roots == roots:
            matches.append(path)
    if len(matches) != 1:
        raise IntegrationError("expected exactly one active manifest, found {}".format(len(matches)))
    return matches[0]
```

Keep `restore_manifest()` as the file restoration primitive. The later CLI task runs the post-restore build and changes `state` to `failed` if that build fails.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: `9 passed`.

- [ ] **Step 5: Commit transaction support**

```powershell
git add -- scripts/citysample_argus_integration.py tests/test_citysample_argus_integration.py
git commit -m "Add CitySample integration transactions"
```

### Task 3: Add the UE 5.8 asset installer and verifier

**Files:**
- Create: `scripts/citysample_argus_assets.py`
- Create: `tests/test_citysample_argus_assets.py`

- [ ] **Step 1: Write fake-Unreal asset tests**

Create `tests/test_citysample_argus_assets.py` with the complete minimal fake below. The fake covers Python orchestration only; Task 6's UE 5.8 headless run remains authoritative for UObject behavior:

```python
import importlib
import json
import sys
import types

import pytest


class FakeObject:
    def get_editor_property(self, name):
        return getattr(self, name)

    def set_editor_property(self, name, value):
        setattr(self, name, value)


class FakeKey:
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name

    def __eq__(self, other):
        return isinstance(other, FakeKey) and self.name == other.name


class FakeMapping(FakeObject):
    def __init__(self, action, key):
        self.action = action
        self.key = key


class FakeMappingData(FakeObject):
    def __init__(self, context):
        self.context = context

    @property
    def mappings(self):
        return self.context.current_mappings


class FakeContext(FakeObject):
    def __init__(self, action, mapped):
        self.current_mappings = (
            [FakeMapping(action, FakeKey("F9"))] if mapped else []
        )
        self.default_key_mappings = FakeMappingData(self)
        self.unmapped = []
        self.mapped = []

    def unmap_key(self, action, key):
        self.unmapped.append((action, str(key)))
        self.current_mappings[:] = [
            mapping
            for mapping in self.current_mappings
            if not (mapping.action is action and str(mapping.key) == str(key))
        ]

    def map_key(self, action, key):
        self.mapped.append((action, str(key)))
        self.current_mappings.append(FakeMapping(action, key))


class FakeUnreal:
    def __init__(self, action_exists):
        self.action_exists = action_exists
        self.action = FakeObject()
        self.action.value_type = "Boolean"
        self.context = FakeContext(self.action, action_exists)
        self.default_object = FakeObject()
        self.default_object.capture_action = self.action if action_exists else None
        self.blueprint_class = object()
        self.duplicated = []
        self.saved = []

        module = types.ModuleType("unreal")
        module.InputActionValueType = types.SimpleNamespace(BOOLEAN="Boolean")
        module.Key = FakeKey
        module.get_default_object = lambda blueprint_class: self.default_object
        module.EditorAssetLibrary = types.SimpleNamespace(
            does_asset_exist=self.does_asset_exist,
            duplicate_asset=self.duplicate_asset,
            load_asset=self.load_asset,
            load_blueprint_class=self.load_blueprint_class,
            save_asset=self.save_asset,
        )
        self.module = module

    def does_asset_exist(self, path):
        return self.action_exists

    def duplicate_asset(self, source, destination):
        self.duplicated.append((source, destination))
        self.action_exists = True
        return True

    def load_asset(self, path):
        if path.endswith("IA_PM_ArgusCapture"):
            return self.action if self.action_exists else None
        if path.endswith("IM_PM_Simple_MappingContext"):
            return self.context
        return None

    def load_blueprint_class(self, path):
        return self.blueprint_class

    def save_asset(self, path):
        self.saved.append(path)
        return True


def import_asset_module(monkeypatch, action_exists):
    fake = FakeUnreal(action_exists)
    monkeypatch.setitem(sys.modules, "unreal", fake.module)
    sys.modules.pop("scripts.citysample_argus_assets", None)
    module = importlib.import_module("scripts.citysample_argus_assets")
    return module, fake


def test_install_assets_creates_action_maps_f9_sets_cdo_and_saves(monkeypatch, tmp_path):
    module, fake = import_asset_module(monkeypatch, action_exists=False)

    result = module.install_assets()

    assert result == {
        "action": module.ACTION_PATH,
        "f9_mappings": 1,
        "capture_action_set": True,
    }
    assert fake.duplicated == [(module.SOURCE_ACTION_PATH, module.ACTION_PATH)]
    assert fake.context.unmapped == [(fake.action, "F9")]
    assert fake.context.mapped == [(fake.action, "F9")]
    assert fake.default_object.capture_action is fake.action
    assert fake.saved == [module.ACTION_PATH, module.CONTEXT_PATH, module.BLUEPRINT_PATH]


def test_install_assets_is_idempotent(monkeypatch):
    module, fake = import_asset_module(monkeypatch, action_exists=True)

    module.install_assets()
    module.install_assets()

    assert fake.duplicated == []
    assert len(fake.context.current_mappings) == 1


def test_verify_assets_rejects_missing_f9_mapping(monkeypatch):
    module, fake = import_asset_module(monkeypatch, action_exists=True)
    fake.context.current_mappings.clear()

    with pytest.raises(RuntimeError, match="exactly one F9 mapping"):
        module.verify_assets()


def test_main_writes_success_result(monkeypatch, tmp_path):
    module, _ = import_asset_module(monkeypatch, action_exists=True)
    result_path = tmp_path / "result.json"

    assert module.main(["verify", "--result", str(result_path)]) == 0

    assert json.loads(result_path.read_text(encoding="utf-8"))["ok"] is True
```

- [ ] **Step 2: Run the asset tests and verify RED**

Run:

```powershell
python -m pytest tests/test_citysample_argus_assets.py -q
```

Expected: collection fails because `scripts.citysample_argus_assets` does not exist.

- [ ] **Step 3: Implement the UE asset script**

Create `scripts/citysample_argus_assets.py` with this public behavior:

```python
"""Install or verify the CitySample assets used by Argus Photo Mode capture."""

import argparse
import json
from pathlib import Path

import unreal


ACTION_PATH = "/Game/Input/PhotoMode/IA_PM_ArgusCapture"
SOURCE_ACTION_PATH = "/Game/Input/PhotoMode/IA_PM_AutoFocus"
CONTEXT_PATH = "/Game/Input/PhotoMode/IM_PM_Simple_MappingContext"
BLUEPRINT_PATH = "/Game/Gameplay/Framework/BP_PhotoModeComponent"


def _load_assets():
    action = unreal.EditorAssetLibrary.load_asset(ACTION_PATH)
    context = unreal.EditorAssetLibrary.load_asset(CONTEXT_PATH)
    blueprint_class = unreal.EditorAssetLibrary.load_blueprint_class(BLUEPRINT_PATH)
    if not action or not context or not blueprint_class:
        raise RuntimeError("Argus Photo Mode assets could not be loaded")
    return action, context, unreal.get_default_object(blueprint_class)


def _mapping_action(mapping):
    return mapping.get_editor_property("action")


def _mapping_key(mapping):
    return str(mapping.get_editor_property("key"))


def verify_assets():
    action, context, default_object = _load_assets()
    if action.get_editor_property("value_type") != unreal.InputActionValueType.BOOLEAN:
        raise RuntimeError("Argus capture action is not Boolean")
    mappings = context.get_editor_property("default_key_mappings").get_editor_property("mappings")
    matches = [
        mapping
        for mapping in mappings
        if _mapping_action(mapping) == action and _mapping_key(mapping) == "F9"
    ]
    if len(matches) != 1:
        raise RuntimeError("Argus capture action must have exactly one F9 mapping")
    if default_object.get_editor_property("capture_action") != action:
        raise RuntimeError("Photo Mode CDO does not reference the Argus capture action")
    return {"action": ACTION_PATH, "f9_mappings": 1, "capture_action_set": True}


def install_assets():
    if not unreal.EditorAssetLibrary.does_asset_exist(ACTION_PATH):
        if not unreal.EditorAssetLibrary.duplicate_asset(SOURCE_ACTION_PATH, ACTION_PATH):
            raise RuntimeError("Argus capture action could not be created")
    action, context, default_object = _load_assets()
    action.set_editor_property("value_type", unreal.InputActionValueType.BOOLEAN)
    key = unreal.Key("F9")
    context.unmap_key(action, key)
    context.map_key(action, key)
    default_object.set_editor_property("capture_action", action)
    for path in (ACTION_PATH, CONTEXT_PATH, BLUEPRINT_PATH):
        if not unreal.EditorAssetLibrary.save_asset(path):
            raise RuntimeError("asset could not be saved: {}".format(path))
    return verify_assets()


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("install", "verify"))
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result_path = Path(args.result)
    if result_path.exists():
        result_path.unlink()
    details = install_assets() if args.mode == "install" else verify_assets()
    result_path.write_text(
        json.dumps({"ok": True, "details": details}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("ARGUS_CITYSAMPLE_ASSETS_{}_OK".format(args.mode.upper()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run asset tests and compile the script**

Run:

```powershell
python -m pytest tests/test_citysample_argus_assets.py -q
python -m compileall -q scripts/citysample_argus_assets.py
```

Expected: `4 passed`; compileall exits `0` with no output.

- [ ] **Step 5: Commit the asset tool**

```powershell
git add -- scripts/citysample_argus_assets.py tests/test_citysample_argus_assets.py
git commit -m "Automate CitySample capture assets"
```

### Task 4: Add process orchestration and the install/verify/restore CLI

**Files:**
- Modify: `scripts/citysample_argus_integration.py`
- Modify: `tests/test_citysample_argus_integration.py`

- [ ] **Step 1: Add failing command and orchestration tests**

Append tests for these public functions and inject a fake runner so no UE process starts:

```python
from scripts.citysample_argus_integration import (
    _tasklist_has_unreal_editor,
    asset_command,
    build_command,
    install_integration,
    verify_integration,
)


def make_legacy_backup(root, citysample_root):
    for relative, legacy_relative in LEGACY_BACKUP_PATHS.items():
        destination = root / legacy_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(citysample_root / relative, destination)
    return root


def make_tool_roots(tmp_path, citysample_root):
    argus_root = tmp_path / "Argus"
    asset_script = argus_root / "scripts/citysample_argus_assets.py"
    asset_script.parent.mkdir(parents=True)
    asset_script.write_text("# test asset script\n", encoding="utf-8")

    ue_root = tmp_path / "UE"
    build = ue_root / "Engine/Build/BatchFiles/Build.bat"
    editor = ue_root / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
    build.parent.mkdir(parents=True)
    editor.parent.mkdir(parents=True)
    build.write_text("@exit /b 0\n", encoding="utf-8")
    editor.write_bytes(b"")
    (citysample_root / "CitySample.uproject").write_text("{}\n", encoding="utf-8")
    return argus_root, ue_root


def test_tasklist_detection_is_case_insensitive():
    assert _tasklist_has_unreal_editor(
        '"UnrealEditor.exe","52508","Console","1","2,048 K"'
    )
    assert not _tasklist_has_unreal_editor(
        'INFO: No tasks are running which match the specified criteria.'
    )


def test_build_command_uses_parameterized_roots(tmp_path):
    ue_root = tmp_path / "UE"
    citysample_root = tmp_path / "CitySample"

    command = build_command(ue_root, citysample_root)

    assert command == [
        str(ue_root / "Engine/Build/BatchFiles/Build.bat"),
        "CitySampleEditor",
        "Win64",
        "Development",
        "-Project={}".format(citysample_root / "CitySample.uproject"),
        "-WaitMutex",
        "-FromMsBuild",
    ]


def test_asset_command_uses_execute_python_script(tmp_path):
    command = asset_command(
        tmp_path / "UE",
        tmp_path / "CitySample",
        tmp_path / "Argus",
        "verify",
        tmp_path / "result.json",
    )

    assert command[0].endswith("UnrealEditor-Cmd.exe")
    assert command[1].endswith("CitySample.uproject")
    assert command[2].startswith("-ExecutePythonScript=")
    assert "citysample_argus_assets.py" in command[2]
    assert " verify " in command[2]
    assert command[-2:] == ["-unattended", "-nop4"]


def test_adopt_install_builds_and_verifies_without_rewriting_sources(tmp_path, monkeypatch):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = read_source_files(citysample_root)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)
    patched = patch_source_texts(
        original[0][0], original[1][0], original[2][0], argus_root
    )
    write_source_files(citysample_root, patched, original)
    action = citysample_root / ACTION_REL
    action.parent.mkdir(parents=True, exist_ok=True)
    action.write_bytes(b"action")
    calls = []
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._run",
        lambda command: calls.append(command),
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._require_asset_result",
        lambda path: {"ok": True},
    )

    manifest_path = install_integration(
        argus_root,
        citysample_root,
        ue_root,
        adopt_backup=legacy,
        commit="abc123",
        stamp="20260725_010203",
    )

    expected_sources = (
        (patched[0], original[0][1], original[0][2]),
        (patched[1], original[1][1], original[1][2]),
        (patched[2], original[2][1], original[2][2]),
    )
    assert read_source_files(citysample_root) == expected_sources
    assert len(calls) == 2
    assert "CitySampleEditor" in calls[0]
    assert " verify " in calls[1][2]
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "installed"


def test_fresh_install_rolls_back_when_asset_stage_fails(tmp_path, monkeypatch):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = {relative: (citysample_root / relative).read_bytes() for relative in EXISTING_FILES}
    calls = []

    def fake_run(command):
        calls.append(command)
        if "-ExecutePythonScript=" in " ".join(command):
            raise IntegrationError("asset stage failed")

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fake_run)

    with pytest.raises(IntegrationError, match="asset stage failed"):
        install_integration(
            argus_root,
            citysample_root,
            ue_root,
            commit="abc123",
            stamp="20260725_010203",
        )

    for relative, data in original.items():
        assert (citysample_root / relative).read_bytes() == data
    assert not (citysample_root / ACTION_REL).exists()
    manifest_path = (
        citysample_root
        / "ArgusBackups/argus_integration/20260725_010203/manifest.json"
    )
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "failed"
```

- [ ] **Step 2: Run orchestration tests and verify RED**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: imports fail for `build_command`, `asset_command`, and the integration entrypoints.

- [ ] **Step 3: Implement commands, validation, transaction phases, and CLI**

Add these functions to `scripts/citysample_argus_integration.py`:

```python
def build_command(ue_root, citysample_root):
    return [
        str(Path(ue_root) / "Engine/Build/BatchFiles/Build.bat"),
        "CitySampleEditor",
        "Win64",
        "Development",
        "-Project={}".format(Path(citysample_root) / "CitySample.uproject"),
        "-WaitMutex",
        "-FromMsBuild",
    ]


def asset_command(ue_root, citysample_root, argus_root, mode, result_path):
    editor = Path(ue_root) / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"
    project = Path(citysample_root) / "CitySample.uproject"
    script = Path(argus_root) / "scripts/citysample_argus_assets.py"
    execute = '-ExecutePythonScript="{} {} --result \\"{}\\""'.format(
        script.as_posix(), mode, Path(result_path).as_posix()
    )
    return [str(editor), str(project), execute, "-unattended", "-nop4"]


def _run(command):
    completed = subprocess.run(command, check=False)
    if completed.returncode:
        raise IntegrationError(
            "command failed with exit code {}: {}".format(
                completed.returncode, subprocess.list2cmdline(command)
            )
        )


def _tasklist_has_unreal_editor(output):
    return "unrealeditor.exe" in output.casefold()


def require_editor_closed():
    completed = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq UnrealEditor.exe", "/FO", "CSV", "/NH"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise IntegrationError("tasklist failed while checking UnrealEditor.exe")
    if _tasklist_has_unreal_editor(completed.stdout):
        raise IntegrationError("close UnrealEditor.exe before running integration commands")


def _require_asset_result(result_path):
    path = Path(result_path)
    if not path.is_file():
        raise IntegrationError("UE asset result was not written")
    result = json.loads(path.read_text(encoding="utf-8"))
    if result.get("ok") is not True:
        raise IntegrationError("UE asset verification failed")
    return result


def _git_commit(argus_root):
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(argus_root),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def validate_roots(argus_root, citysample_root, ue_root):
    required = (
        Path(argus_root) / "scripts/citysample_argus_assets.py",
        Path(citysample_root) / "CitySample.uproject",
        Path(ue_root) / "Engine/Build/BatchFiles/Build.bat",
        Path(ue_root) / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe",
    ) + tuple(Path(citysample_root) / relative for relative in EXISTING_FILES)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise IntegrationError("required file is missing: {}".format(", ".join(missing)))


def _complete_phase(manifest_path, phase):
    manifest_path, manifest = load_manifest(manifest_path)
    if phase not in manifest["completed_phases"]:
        manifest["completed_phases"].append(phase)
        _write_json_atomic(manifest_path, manifest)


def _record_installed_hashes(manifest_path):
    manifest_path, manifest = load_manifest(manifest_path)
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        row["installed_sha256"] = sha256_file(path) if path.exists() else None
    if "verify" not in manifest["completed_phases"]:
        manifest["completed_phases"].append("verify")
    manifest["state"] = "installed"
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def _run_assets(argus_root, citysample_root, ue_root, mode, manifest_path):
    result_path = Path(manifest_path).parent / "asset_result.json"
    if result_path.exists():
        result_path.unlink()
    _run(asset_command(ue_root, citysample_root, argus_root, mode, result_path))
    return _require_asset_result(result_path)


def _verify_host(manifest_path):
    manifest_path, manifest = load_manifest(manifest_path)
    if manifest["state"] != "installed":
        raise IntegrationError("manifest is not installed")
    _check_installed_hashes(manifest_path, manifest)
    citysample_root = Path(manifest["roots"]["citysample"])
    formats = read_source_files(citysample_root)
    verify_source_texts(
        formats[0][0],
        formats[1][0],
        formats[2][0],
        Path(manifest["roots"]["argus"]),
    )
    return manifest_path, manifest


def verify_integration(manifest_path):
    manifest_path, manifest = _verify_host(manifest_path)
    citysample_root = Path(manifest["roots"]["citysample"])
    _run_assets(
        Path(manifest["roots"]["argus"]),
        citysample_root,
        Path(manifest["roots"]["ue"]),
        "verify",
        manifest_path,
    )
    return manifest_path


def install_integration(
    argus_root,
    citysample_root,
    ue_root,
    adopt_backup=None,
    commit=None,
    stamp=None,
):
    argus_root = Path(argus_root).resolve()
    citysample_root = Path(citysample_root).resolve()
    ue_root = Path(ue_root).resolve()
    validate_roots(argus_root, citysample_root, ue_root)
    formats = read_source_files(citysample_root)
    installed = True
    try:
        verify_source_texts(
            formats[0][0], formats[1][0], formats[2][0], argus_root
        )
    except IntegrationError:
        installed = False
    if installed and not adopt_backup:
        return verify_integration(find_manifest(argus_root, citysample_root, ue_root))
    if installed != bool(adopt_backup):
        raise IntegrationError(
            "--adopt-backup is required only for an existing installation"
        )
    manifest_path = create_manifest(
        citysample_root,
        argus_root,
        ue_root,
        adopt_backup=adopt_backup,
        commit=commit or _git_commit(argus_root),
        stamp=stamp,
    )
    try:
        if not installed:
            patched = patch_source_texts(
                formats[0][0], formats[1][0], formats[2][0], argus_root
            )
            write_source_files(citysample_root, patched, formats)
        _complete_phase(manifest_path, "source")
        _run(build_command(ue_root, citysample_root))
        _complete_phase(manifest_path, "build")
        _run_assets(
            argus_root,
            citysample_root,
            ue_root,
            "verify" if installed else "install",
            manifest_path,
        )
        _complete_phase(manifest_path, "assets")
        _record_installed_hashes(manifest_path)
        _verify_host(manifest_path)
        return manifest_path
    except Exception as exc:
        rollback_error = None
        if not installed:
            try:
                restore_manifest(manifest_path, check_drift=False)
                _run(build_command(ue_root, citysample_root))
            except Exception as rollback_exc:
                rollback_error = str(rollback_exc)
        manifest_path, manifest = load_manifest(manifest_path)
        manifest["state"] = "failed"
        manifest["error"] = str(exc)
        if not installed:
            manifest["rollback_completed"] = rollback_error is None
        if rollback_error:
            manifest["rollback_error"] = rollback_error
        _write_json_atomic(manifest_path, manifest)
        raise


def restore_integration(manifest_path):
    manifest_path, manifest = load_manifest(manifest_path)
    if manifest["state"] != "installed":
        raise IntegrationError("manifest is not installed")
    ue_root = Path(manifest["roots"]["ue"])
    citysample_root = Path(manifest["roots"]["citysample"])
    restore_manifest(manifest_path)
    try:
        _run(build_command(ue_root, citysample_root))
    except Exception as exc:
        manifest_path, manifest = load_manifest(manifest_path)
        manifest["state"] = "failed"
        manifest["error"] = str(exc)
        _write_json_atomic(manifest_path, manifest)
        raise
    return manifest_path


def _parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("install", "verify", "restore"))
    parser.add_argument("--argus-root", type=Path, default=DEFAULT_ARGUS_ROOT)
    parser.add_argument("--citysample-root", type=Path, default=DEFAULT_CITYSAMPLE_ROOT)
    parser.add_argument("--ue-root", type=Path, default=DEFAULT_UE_ROOT)
    parser.add_argument("--adopt-backup", type=Path)
    parser.add_argument("--manifest", type=Path)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    require_editor_closed()
    if args.command == "install":
        result = install_integration(
            args.argus_root,
            args.citysample_root,
            args.ue_root,
            adopt_backup=args.adopt_backup,
        )
    else:
        manifest = find_manifest(
            args.argus_root,
            args.citysample_root,
            args.ue_root,
            args.manifest,
        )
        result = (
            verify_integration(manifest)
            if args.command == "verify"
            else restore_integration(manifest)
        )
    print(result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except IntegrationError as exc:
        print("Argus CitySample integration failed: {}".format(exc), file=sys.stderr)
        raise SystemExit(1)
```

- [ ] **Step 4: Run all host integration tests**

Run:

```powershell
python -m pytest tests/test_citysample_argus_integration.py -q
```

Expected: all tests in the file pass.

- [ ] **Step 5: Commit the host CLI**

```powershell
git add -- scripts/citysample_argus_integration.py tests/test_citysample_argus_integration.py
git commit -m "Orchestrate CitySample integration lifecycle"
```

### Task 5: Reuse the source verifier and document the workflow

**Files:**
- Modify: `tests/test_citysample_photo_mode_capture_integration.py`
- Modify: `docs/workflow.md`

- [ ] **Step 1: Replace duplicated source assertions with the shared verifier**

Add the shared-verifier import and replace the existing test body with this exact version; keep the file's current `CITYSAMPLE_ROOT` skip marker:

```python
from scripts.citysample_argus_integration import verify_source_texts


def test_photo_mode_source_contains_argus_capture_binding():
    header = (
        CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.h"
    ).read_text(encoding="utf-8-sig")
    source = (
        CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.cpp"
    ).read_text(encoding="utf-8-sig")
    build = (CITYSAMPLE_ROOT / "Source/CitySample/CitySample.Build.cs").read_text(
        encoding="utf-8-sig"
    )

    verify_source_texts(
        header,
        source,
        build,
        Path(r"D:\Study\Code\Python\UE\cv\Argus"),
    )

    binding_anchor = source.index("if (CaptureAction)")
    binding_start = source.rfind("#if WITH_EDITOR", 0, binding_anchor)
    binding_end = source.index("#endif", binding_anchor) + len("#endif")
    binding_block = source[binding_start:binding_end]
    assert binding_block.count("#if WITH_EDITOR") == 1
    assert binding_block.count("#endif") == 1

    handler_start = source.index("void UPhotoModeComponent::CaptureActionBinding()")
    handler_end = source.index("\n#endif\n}", handler_start) + len("\n#endif\n}")
    handler = source[handler_start:handler_end]
    assert handler.startswith(
        "void UPhotoModeComponent::CaptureActionBinding()\n{\n#if WITH_EDITOR"
    )
    assert handler.endswith("#endif\n}")

    editor_start = build.index("if (Target.bBuildEditor == true)\n\t\t{")
    editor_end = build.index("\n\t\t}", editor_start) + len("\n\t\t}")
    editor_block = build[editor_start:editor_end]
    assert 'PrivateDependencyModuleNames.Add("PythonScriptPlugin")' in editor_block
```

- [ ] **Step 2: Run the source-contract tests**

Run:

```powershell
python -m pytest tests/test_citysample_photo_mode_capture_integration.py tests/test_citysample_argus_integration.py -q
```

Expected: all focused tests pass.

- [ ] **Step 3: Add the solidification workflow to `docs/workflow.md`**

Document these exact commands:

```powershell
python scripts/citysample_argus_integration.py install `
  --adopt-backup 'E:\UnrealProject\CitySample\ArgusBackups\20260724_player_capture'

python scripts/citysample_argus_integration.py verify --manifest '<manifest.json>'

python scripts/citysample_argus_integration.py restore --manifest '<manifest.json>'
```

Explain that all three roots have current-machine defaults and may be overridden. State that the editor must be closed, restore refuses drift, `.uasset` files stay outside Git, and no tag is created.

- [ ] **Step 4: Run repository verification**

Run:

```powershell
python -m pytest -q
python -m compileall -q argus_core argus_backends scripts tests
git diff --check
git status --short --branch
```

Expected: all tests pass, compileall and diff-check exit `0`, and only intended tracked changes plus untracked `AGENTS.md` appear.

- [ ] **Step 5: Commit tests and documentation**

```powershell
git add -- tests/test_citysample_photo_mode_capture_integration.py docs/workflow.md
git commit -m "Document CitySample integration lifecycle"
```

### Task 6: Adopt the current installation, verify UE 5.8, merge, and push

**Files:**
- External output: `E:\UnrealProject\CitySample\ArgusBackups\argus_integration\<timestamp>\manifest.json`
- Verify external: six managed CitySample files

- [ ] **Step 1: Run feature-branch verification and two reviews**

From the isolated feature worktree, run:

```powershell
python -m pytest -q
python -m compileall -q argus_core argus_backends scripts tests
git diff --check
git status --short --branch
```

Expected: all checks pass and only `AGENTS.md` is untracked. Then run two sequential reviews over the implementation commit range:

1. Specification compliance against `docs/superpowers/specs/2026-07-25-citysample-integration-solidification-design.md`.
2. Code quality, transaction safety, Windows path quoting, UE command-line behavior, and restore drift protection.

Fix every Critical or Important finding, rerun the affected tests, and repeat the relevant review.

- [ ] **Step 2: Fast-forward the implementation into local `main` without cleaning up**

From the main worktree at `D:\Study\Code\Python\UE\cv\Argus`, run:

```powershell
git checkout main
git merge --ff-only argus/citysample-integration-solidification
```

Expected: fast-forward succeeds. Keep the feature branch and isolated worktree until live UE acceptance is complete, so fixes can still be committed there and fast-forwarded again.

- [ ] **Step 3: Save and close the visible CitySample Editor safely**

With PIE stopped, save dirty packages through UE Python:

```python
import unreal
assert unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
```

Then close the editor window through Slate/Win32 `WM_CLOSE` or `Alt+F4`, wait for PID exit, and confirm:

```powershell
Get-Process UnrealEditor -ErrorAction SilentlyContinue
```

Expected: no process output. Do not call `unreal.SystemLibrary.quit_editor()`.

- [ ] **Step 4: Run current-state adoption from the canonical main worktree**

From `D:\Study\Code\Python\UE\cv\Argus`, run:

```powershell
python scripts/citysample_argus_integration.py install `
  --argus-root 'D:\Study\Code\Python\UE\cv\Argus' `
  --citysample-root 'E:\UnrealProject\CitySample' `
  --ue-root 'E:\UE_5.8' `
  --adopt-backup 'E:\UnrealProject\CitySample\ArgusBackups\20260724_player_capture'
```

Expected:

- CitySampleEditor build exits `0`.
- UE headless asset verify writes `asset_result.json` with `ok: true`.
- A new manifest is printed with state `installed`.
- The existing five backup files are copied; `UnexpectedSaves` is ignored.
- No CitySample source or asset hash changes during adoption.

- [ ] **Step 5: Run explicit verify with the printed manifest**

```powershell
python scripts/citysample_argus_integration.py verify --manifest '<printed-manifest-path>'
```

Expected: host hashes/source contract and UE asset assertions all pass.

- [ ] **Step 6: Run final verification on merged `main`**

```powershell
python -m pytest -q
python -m compileall -q argus_core argus_backends scripts tests
git diff --check
git status --short --branch
```

Expected: all tests pass; only `AGENTS.md` remains untracked after commits.

- [ ] **Step 7: Apply any live-acceptance fix through the feature branch**

If Steps 4-6 expose a defect, commit the minimal fix in the still-existing feature worktree, rerun its focused tests, then fast-forward `main` again:

```powershell
git merge --ff-only argus/citysample-integration-solidification
```

Repeat Steps 4-6 after every such fix. If no defect is found, make no commit in this step.

- [ ] **Step 8: Remove the clean feature worktree and branch**

After live acceptance and merged-main verification pass, confirm the feature worktree is clean, then run from the main worktree:

```powershell
git worktree remove '<isolated-worktree-path>'
git branch -d argus/citysample-integration-solidification
```

Expected: the isolated worktree and already-merged feature branch are removed. Do not remove a dirty worktree.

- [ ] **Step 9: Push `main` without a tag**

```powershell
git push origin main
git fetch origin main
git rev-parse main
git rev-parse origin/main
```

Expected: push succeeds and the two commit IDs are identical. Do not create a tag or PR.

## Local UE 5.8 references used by this plan

- `D:\UE58Knowledge\web\markdown\scripting-the-unreal-editor-using-python.md`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\EditorUtilities\EditorPythonExecuter.cpp`
- `E:\UE_5.8\Engine\Plugins\Experimental\PythonScriptPlugin\Source\PythonScriptPlugin\Private\PythonScriptCommandlet.cpp`
- `E:\UE_5.8\Engine\Plugins\EnhancedInput\Source\EnhancedInput\Public\InputMappingContext.h`
- `E:\UE_5.8\Engine\Plugins\EnhancedInput\Source\EnhancedInput\Private\InputMappingContext.cpp`
- `E:\UE_5.8\Engine\Plugins\EnhancedInput\Source\EnhancedInput\Public\InputAction.h`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.h`
- `E:\UnrealProject\CitySample\Source\CitySample\Camera\PhotoModeComponent.cpp`
- `E:\UnrealProject\CitySample\Source\CitySample\CitySample.Build.cs`
