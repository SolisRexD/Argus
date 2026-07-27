"""Install, verify, or restore the Argus CitySample integration."""

from __future__ import annotations

import codecs
import hashlib
import json
import os
import shutil
import tempfile
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
MANIFEST_PHASES = {"backup", "source", "build", "assets", "verify", "restore"}
LEGACY_BACKUP_PATHS = {
    HEADER_REL: Path("Source/PhotoModeComponent.h"),
    SOURCE_REL: Path("Source/PhotoModeComponent.cpp"),
    BUILD_REL: Path("Source/CitySample.Build.cs"),
    MAPPING_REL: Path("Content/IM_PM_Simple_MappingContext.uasset"),
    BLUEPRINT_REL: Path("Content/BP_PhotoModeComponent.uasset"),
}


class IntegrationError(RuntimeError):
    pass


def _resolved_within(path, root, label):
    root = Path(root).resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise IntegrationError("{} escapes its root".format(label))
    return resolved


def _is_sha256(value):
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdefABCDEF" for character in value)
    )


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_text(path):
    raw = Path(path).read_bytes()
    bom = raw.startswith(codecs.BOM_UTF8)
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
    return text.replace("\r\n", "\n").replace("\r", "\n"), bom, newline


def read_source_files(citysample_root):
    root = Path(citysample_root)
    return tuple(
        _read_text(root / relative)
        for relative in (HEADER_REL, SOURCE_REL, BUILD_REL)
    )


def _write_text_atomic(path, text, bom, newline):
    path = Path(path)
    payload = text.replace("\n", newline).encode("utf-8")
    if bom:
        payload = codecs.BOM_UTF8 + payload
    _replace_bytes_atomic(path, payload)


def write_source_files(citysample_root, texts, formats):
    root = Path(citysample_root)
    for relative, text, (_, bom, newline) in zip(
        (HEADER_REL, SOURCE_REL, BUILD_REL), texts, formats
    ):
        _write_text_atomic(root / relative, text, bom, newline)


def _write_json_atomic(path, value):
    path = Path(path)
    _replace_bytes_atomic(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _temporary_path(path):
    descriptor, name = tempfile.mkstemp(
        prefix=Path(path).name + ".",
        suffix=".tmp",
        dir=Path(path).parent,
    )
    os.close(descriptor)
    return Path(name)


def _replace_bytes_atomic(path, payload):
    path = Path(path)
    temporary = _temporary_path(path)
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_file_atomic(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = _temporary_path(destination)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _backup_source(adopt_backup, relative, live_path):
    if adopt_backup is None:
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
    stamp_path = Path(stamp)
    if len(stamp_path.parts) != 1 or stamp_path.name in ("", ".", ".."):
        raise IntegrationError("backup stamp must be one directory name")
    stamp = stamp_path.name
    backup_dir = citysample_root / "ArgusBackups/argus_integration" / stamp
    if os.path.lexists(backup_dir):
        raise IntegrationError("backup already exists: {}".format(backup_dir))
    sources = []
    adopt_root = Path(adopt_backup).resolve() if adopt_backup is not None else None
    for relative in EXISTING_FILES:
        live_path = _resolved_within(
            citysample_root / relative, citysample_root, "managed file"
        )
        source = _backup_source(adopt_backup, relative, live_path)
        source = _resolved_within(
            source,
            adopt_root or citysample_root,
            "adopt backup" if adopt_root else "managed file",
        )
        if not live_path.is_file() or not source.is_file():
            raise IntegrationError("managed file is missing: {}".format(relative))
        sources.append((relative, source))
    action_path = citysample_root / ACTION_REL
    if adopt_backup is None:
        if os.path.lexists(action_path):
            raise IntegrationError("action already exists: {}".format(ACTION_REL))
    elif action_path.is_symlink() or not action_path.is_file():
        raise IntegrationError("adopt action is missing or invalid: {}".format(ACTION_REL))

    backup_dir.mkdir(parents=True)
    try:
        files_dir = backup_dir / "files"
        files_dir.mkdir()
        rows = []
        for relative, source in sources:
            backup_path = files_dir / relative
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, backup_path)
            rows.append(
                {
                    "path": relative.as_posix(),
                    "backup": backup_path.relative_to(backup_dir).as_posix(),
                    "created": False,
                    "original_sha256": sha256_file(backup_path),
                    "installed_sha256": None,
                }
            )
        rows.append(
            {
                "path": ACTION_REL.as_posix(),
                "backup": None,
                "created": True,
                "original_sha256": None,
                "installed_sha256": None,
            }
        )
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
            "adopted": adopt_backup is not None,
            "completed_phases": ["backup"],
            "files": rows,
        }
        manifest_path = backup_dir / "manifest.json"
        _write_json_atomic(manifest_path, manifest)
        return manifest_path
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


def _validate_manifest(path, data):
    def invalid(reason):
        raise IntegrationError("invalid manifest: {}".format(reason))

    if not isinstance(data, dict) or type(data.get("schema_version")) is not int:
        invalid("unsupported schema")
    if data["schema_version"] != 1:
        invalid("unsupported schema")
    state = data.get("state")
    if state not in {
        "installing",
        "installed",
        "restoring",
        "restored",
        "failed",
    }:
        invalid("unsupported state")
    phases = data.get("completed_phases")
    if (
        not isinstance(phases, list)
        or not phases
        or not all(isinstance(phase, str) for phase in phases)
        or len(phases) != len(set(phases))
        or "backup" not in phases
        or not set(phases) <= MANIFEST_PHASES
    ):
        invalid("completed phases")
    roots = data.get("roots")
    if not isinstance(roots, dict) or set(roots) != {"argus", "citysample", "ue"}:
        invalid("roots")
    if not all(isinstance(value, str) for value in roots.values()):
        invalid("roots")
    try:
        citysample_root = Path(roots["citysample"]).resolve()
        backup_root = (
            citysample_root / "ArgusBackups/argus_integration"
        ).resolve()
        backup_dir = Path(data.get("backup_dir")).resolve()
    except (OSError, RuntimeError, TypeError, ValueError):
        invalid("paths")
    if path.name != "manifest.json" or path.parent.parent != backup_root:
        invalid("location")
    if backup_dir != path.parent:
        invalid("backup directory")

    rows = data.get("files")
    if not isinstance(rows, list) or len(rows) != len(MANAGED_FILES):
        invalid("managed files")
    expected = {relative.as_posix(): relative for relative in MANAGED_FILES}
    seen = set()
    required_keys = {
        "path",
        "backup",
        "created",
        "original_sha256",
        "installed_sha256",
    }
    for row in rows:
        if not isinstance(row, dict) or not required_keys <= set(row):
            invalid("file row")
        row_path = row["path"]
        if not isinstance(row_path, str):
            invalid("file path")
        relative = expected.get(row_path)
        if relative is None or row_path in seen:
            invalid("managed files")
        seen.add(row_path)
        installed_hash = row["installed_sha256"]
        if state == "installed" and not _is_sha256(installed_hash):
            invalid("installed hash")
        if installed_hash is not None and not _is_sha256(installed_hash):
            invalid("installed hash")
        try:
            _resolved_within(
                citysample_root / relative,
                citysample_root,
                "manifest live path",
            )
        except IntegrationError as exc:
            invalid(str(exc))
        if relative == ACTION_REL:
            if (
                row["created"] is not True
                or row["backup"] is not None
                or row["original_sha256"] is not None
            ):
                invalid("action row")
            continue
        expected_backup = (Path("files") / relative).as_posix()
        if (
            row["created"] is not False
            or row["backup"] != expected_backup
            or not _is_sha256(row["original_sha256"])
        ):
            invalid("existing file row")
        try:
            _resolved_within(
                path.parent / row["backup"],
                path.parent,
                "manifest backup path",
            )
        except IntegrationError as exc:
            invalid(str(exc))
    if seen != set(expected):
        invalid("managed files")


def load_manifest(manifest_path):
    path = Path(manifest_path).resolve()
    data = json.loads(path.read_text(encoding="utf-8"))
    _validate_manifest(path, data)
    return path, data


def _check_installed_hashes(manifest_path, manifest):
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        actual = sha256_file(path) if path.is_file() else None
        if row.get("installed_sha256") != actual:
            raise IntegrationError("installed file drift: {}".format(row["path"]))


def _preflight_restore(manifest_path, manifest):
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        live_path = citysample_root / row["path"]
        if os.path.lexists(live_path) and not live_path.is_file():
            raise IntegrationError("invalid managed path: {}".format(row["path"]))
        if row["created"]:
            continue
        backup_path = manifest_path.parent / row["backup"]
        if not backup_path.is_file():
            raise IntegrationError("backup is missing: {}".format(row["path"]))
        if sha256_file(backup_path) != row["original_sha256"]:
            raise IntegrationError("backup hash mismatch: {}".format(row["path"]))


def restore_manifest(manifest_path, check_drift=True):
    manifest_path, manifest = load_manifest(manifest_path)
    if check_drift:
        if manifest["state"] != "installed":
            raise IntegrationError("manifest is not installed")
        _check_installed_hashes(manifest_path, manifest)
    _preflight_restore(manifest_path, manifest)
    manifest["state"] = "restoring"
    _write_json_atomic(manifest_path, manifest)
    citysample_root = Path(manifest["roots"]["citysample"])
    for row in manifest["files"]:
        if not row["created"]:
            _copy_file_atomic(
                manifest_path.parent / row["backup"],
                citysample_root / row["path"],
            )
    action_path = citysample_root / ACTION_REL
    if os.path.lexists(action_path):
        action_path.unlink()
    manifest["state"] = "restored"
    manifest["completed_phases"].append("restore")
    _write_json_atomic(manifest_path, manifest)
    return manifest_path


def find_manifest(argus_root, citysample_root, ue_root, explicit=None):
    if explicit is not None:
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
        raise IntegrationError(
            "expected exactly one active manifest, found {}".format(len(matches))
        )
    return matches[0]


def _argus_scripts_path(argus_root):
    return (Path(argus_root).resolve() / "scripts").as_posix()


def _is_installed_once(text, anchor, addition, marker):
    return (
        text.count(anchor) == 1
        and text.count(addition) == 1
        and text.count(marker) == 1
        and text.count(anchor + addition) == 1
    )


def _insert_after_once(text, anchor, addition, marker, label):
    addition_count = text.count(addition)
    if addition_count > 1:
        raise IntegrationError("{} is duplicated".format(label))
    if addition_count == 1:
        if not _is_installed_once(text, anchor, addition, marker):
            raise IntegrationError("{} is partial or conflicting".format(label))
        return text
    if marker in text or text.count(anchor) != 1 or anchor + addition in text:
        raise IntegrationError("{} is partial or conflicting".format(label))
    return text.replace(anchor, anchor + addition, 1)


def expected_source_fragments(argus_root):
    scripts_path = _argus_scripts_path(argus_root)
    scripts_literal = ("r" + repr(scripts_path)).replace("\\", "\\\\").replace(
        '"', '\\"'
    )
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
\t\t\"p={scripts_literal}; \"
\t\t\"sys.path.insert(0, p) if p not in sys.path else None; \"
\t\t\"import capture_player_view; \"
\t\t\"capture_player_view.capture_player_view()\"
\t);

\tif (!PythonPlugin->ExecPythonCommand(Command))
\t{
\t\tUE_LOG(LogCitySamplePhotoMode, Error, TEXT(\"Argus capture Python command failed.\"));
\t}
#endif
}""".replace("{scripts_literal}", scripts_literal)
    build_dependency = "\n\t\t\tPrivateDependencyModuleNames.Add(\"PythonScriptPlugin\");\n"
    return {
        "header": (capture_property, capture_declaration),
        "source": (python_include, capture_binding, capture_handler),
        "build": (build_dependency,),
    }


def _source_contract_items(argus_root):
    fragments = expected_source_fragments(argus_root)
    return {
        "header": (
            (
                '\tclass UInputAction* UseAutoFocusAction;',
                fragments["header"][0],
                "UInputAction* CaptureAction",
                "capture property",
            ),
            (
                "\tvoid DisableAutoFocusActionBinding();",
                fragments["header"][1],
                "void CaptureActionBinding();",
                "capture declaration",
            ),
        ),
        "source": (
            (
                '#include "InputMappingContext.h"',
                fragments["source"][0],
                "IPythonScriptPlugin.h",
                "Python include",
            ),
            (
                "\t\tEnhancedInputComponent->BindAction(UseAutoFocusAction, ETriggerEvent::Completed, this, &ThisClass::DisableAutoFocusActionBinding);",
                fragments["source"][1],
                "BindAction(CaptureAction",
                "capture binding",
            ),
            (
                "void UPhotoModeComponent::DisableAutoFocusActionBinding()\n{\n\tDisableAutoFocus();\n}",
                fragments["source"][2],
                "void UPhotoModeComponent::CaptureActionBinding()",
                "capture handler",
            ),
        ),
        "build": (
            (
                "\t\tif (Target.bBuildEditor == true)\n\t\t{",
                fragments["build"][0],
                'PrivateDependencyModuleNames.Add("PythonScriptPlugin")',
                "Python build dependency",
            ),
        ),
    }


def patch_source_texts(header, source, build, argus_root):
    items = _source_contract_items(argus_root)
    texts = {"header": header, "source": source, "build": build}
    counts = []
    for name, contracts in items.items():
        for _, addition, _, label in contracts:
            count = texts[name].count(addition)
            if count > 1:
                raise IntegrationError("{} is duplicated".format(label))
            counts.append(count)
    if any(counts) and not all(count == 1 for count in counts):
        raise IntegrationError("source contract is partial or conflicting")
    for name, contracts in items.items():
        for anchor, addition, marker, label in contracts:
            texts[name] = _insert_after_once(
                texts[name], anchor, addition, marker, label
            )
    verify_source_texts(
        texts["header"], texts["source"], texts["build"], argus_root
    )
    return texts["header"], texts["source"], texts["build"]


def verify_source_texts(header, source, build, argus_root):
    texts = {"header": header, "source": source, "build": build}
    for name, contracts in _source_contract_items(argus_root).items():
        for anchor, addition, marker, _ in contracts:
            if not _is_installed_once(texts[name], anchor, addition, marker):
                raise IntegrationError("{} source contract is not installed".format(name))
    return True
