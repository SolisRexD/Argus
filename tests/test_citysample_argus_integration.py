import ast
import codecs
import ctypes
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.citysample_argus_integration import (
    ACTION_REL,
    BLUEPRINT_REL,
    BUILD_REL,
    EXISTING_FILES,
    HEADER_REL,
    IntegrationError,
    LEGACY_BACKUP_PATHS,
    MANAGED_FILES,
    MAPPING_REL,
    SOURCE_REL,
    _integration_lock,
    _require_asset_result,
    _run,
    _run_assets,
    _tasklist_has_unreal_editor,
    asset_command,
    build_command,
    create_manifest,
    expected_source_fragments,
    find_manifest,
    install_integration,
    load_manifest,
    main,
    patch_source_texts,
    read_source_files,
    require_editor_closed,
    restore_integration,
    restore_manifest,
    sha256_file,
    verify_source_texts,
    verify_integration,
    write_source_files,
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

FULL_INSTALL_PHASES = ["backup", "source", "build", "assets", "verify"]


def make_citysample_tree(tmp_path):
    root = tmp_path / "CitySample"
    files = {
        HEADER_REL: BASE_HEADER.encode("utf-8"),
        SOURCE_REL: BASE_SOURCE.replace("\n", "\r\n").encode("utf-8"),
        BUILD_REL: codecs.BOM_UTF8 + BASE_BUILD.encode("utf-8"),
        MAPPING_REL: b"mapping-before",
        BLUEPRINT_REL: b"blueprint-before",
    }
    assert tuple(files) == EXISTING_FILES
    for relative, data in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


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


def make_installed_integration(tmp_path, stamp="installed-integration"):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = read_source_files(citysample_root)
    manifest_path = create_manifest(
        citysample_root,
        argus_root,
        ue_root,
        commit="abc123",
        stamp=stamp,
    )
    patched = patch_source_texts(
        original[0][0], original[1][0], original[2][0], argus_root
    )
    write_source_files(citysample_root, patched, original)
    action = citysample_root / ACTION_REL
    action.parent.mkdir(parents=True, exist_ok=True)
    action.write_bytes(b"action")
    mark_installed(manifest_path, citysample_root)
    return manifest_path


def reconstruct_ue_windows_command(command):
    argc = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = (
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    )
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    argv = command_line_to_argv(subprocess.list2cmdline(command), ctypes.byref(argc))
    assert argv
    try:
        arguments = [argv[index] for index in range(1, argc.value)]
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = (ctypes.c_void_p,)
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(argv, ctypes.c_void_p))
    rebuilt = []
    for argument in arguments:
        if " " in argument:
            quote = argument.find("=") + 1 if argument.startswith("-") and "=" in argument else 0
            argument = argument[:quote] + '"' + argument[quote:] + '"'
        rebuilt.append(argument)
    return " ".join(rebuilt)


def asset_result_path(command):
    value = command[2].split(' --result \\"', 1)[1]
    return Path(value.rsplit('\\"', 1)[0])


def try_integration_lock_in_subprocess(citysample_root):
    code = """
import sys
from scripts.citysample_argus_integration import IntegrationError, _integration_lock

try:
    with _integration_lock(sys.argv[1]):
        pass
except IntegrationError as exc:
    print(exc)
    raise SystemExit(23)
"""
    return subprocess.run(
        [sys.executable, "-c", code, str(citysample_root)],
        cwd=Path(__file__).parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )


def mark_installed(manifest_path, citysample_root):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        row["installed_sha256"] = sha256_file(path) if path.is_file() else None
    manifest["state"] = "installed"
    manifest["completed_phases"] = FULL_INSTALL_PHASES.copy()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def make_installed_files(citysample_root):
    for relative in EXISTING_FILES:
        (citysample_root / relative).write_bytes(
            b"installed:" + relative.as_posix().encode("utf-8")
        )
    action_path = citysample_root / ACTION_REL
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_path.write_bytes(b"installed-action")
    return action_path


def managed_bytes(citysample_root):
    return {
        relative: (citysample_root / relative).read_bytes()
        if (citysample_root / relative).is_file()
        else None
        for relative in MANAGED_FILES
    }


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


def test_patch_source_texts_cpp_escapes_apostrophe_argus_path():
    patched = patch_source_texts(
        BASE_HEADER,
        BASE_SOURCE,
        BASE_BUILD,
        Path(r"D:\O'Reilly\Argus"),
    )

    cpp_line = next(
        line.strip() for line in patched[1].splitlines() if "p=r" in line
    )
    runtime_python = ast.literal_eval(cpp_line)
    namespace = {}
    exec(runtime_python, namespace)

    assert namespace["p"] == "D:/O'Reilly/Argus/scripts"


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


@pytest.mark.parametrize("operation", [patch_source_texts, verify_source_texts])
def test_source_contract_rejects_fragment_moved_from_anchor(operation):
    argus_root = Path(r"D:\Portable\Argus")
    installed = patch_source_texts(BASE_HEADER, BASE_SOURCE, BASE_BUILD, argus_root)
    capture_property = expected_source_fragments(argus_root)["header"][0]
    moved_header = installed[0].replace(capture_property, "", 1) + capture_property

    with pytest.raises(IntegrationError, match="partial or conflicting|source contract"):
        operation(moved_header, installed[1], installed[2], argus_root)


@pytest.mark.parametrize("operation", [patch_source_texts, verify_source_texts])
def test_source_contract_rejects_extra_marker(operation):
    argus_root = Path(r"D:\Portable\Argus")
    installed = patch_source_texts(BASE_HEADER, BASE_SOURCE, BASE_BUILD, argus_root)
    conflicting_source = installed[1] + "\nBindAction(CaptureAction"

    with pytest.raises(IntegrationError, match="partial or conflicting|source contract"):
        operation(installed[0], conflicting_source, installed[2], argus_root)


def test_source_io_preserves_bom_and_newlines(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    header, source, build = read_source_files(citysample_root)
    patched = patch_source_texts(
        header[0], source[0], build[0], Path(r"D:\Portable\Argus")
    )

    write_source_files(citysample_root, patched, (header, source, build))

    assert (citysample_root / SOURCE_REL).read_bytes() == patched[1].replace(
        "\n", "\r\n"
    ).encode("utf-8")
    assert (citysample_root / BUILD_REL).read_bytes() == (
        codecs.BOM_UTF8 + patched[2].encode("utf-8")
    )


def test_source_atomic_write_preserves_preexisting_fixed_temp(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    formats = read_source_files(citysample_root)
    sentinel = (citysample_root / HEADER_REL).with_name(
        HEADER_REL.name + ".argus.tmp"
    )
    sentinel.write_bytes(b"keep")

    write_source_files(
        citysample_root,
        tuple(item[0] for item in formats),
        formats,
    )

    assert sentinel.read_bytes() == b"keep"


def test_source_atomic_write_cleans_temp_after_replace_failure(tmp_path, monkeypatch):
    citysample_root = make_citysample_tree(tmp_path)
    formats = read_source_files(citysample_root)
    parent = (citysample_root / HEADER_REL).parent
    before = set(parent.iterdir())
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.os.replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(OSError, match="replace failed"):
        write_source_files(
            citysample_root,
            tuple(item[0] for item in formats),
            formats,
        )

    assert set(parent.iterdir()) == before


def test_create_manifest_copies_only_managed_backups(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    action_path = citysample_root / ACTION_REL
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_path.write_bytes(b"installed-action")
    legacy_paths = {
        HEADER_REL: Path("Source/PhotoModeComponent.h"),
        SOURCE_REL: Path("Source/PhotoModeComponent.cpp"),
        BUILD_REL: Path("Source/CitySample.Build.cs"),
        MAPPING_REL: Path("Content/IM_PM_Simple_MappingContext.uasset"),
        BLUEPRINT_REL: Path("Content/BP_PhotoModeComponent.uasset"),
    }
    assert LEGACY_BACKUP_PATHS == legacy_paths
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)
    (legacy / "UnexpectedSaves").mkdir()
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
    action = next(
        row for row in manifest["files"] if row["path"] == ACTION_REL.as_posix()
    )
    assert action["created"] is True
    assert action["backup"] is None
    assert action["original_sha256"] is None


def test_create_manifest_fresh_install_rejects_preexisting_action(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    action_path = citysample_root / ACTION_REL
    action_path.parent.mkdir(parents=True, exist_ok=True)
    action_path.write_bytes(b"pre-existing")

    with pytest.raises(IntegrationError, match="action"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            stamp="fresh-collision",
        )

    assert action_path.read_bytes() == b"pre-existing"
    assert not (
        citysample_root / "ArgusBackups/argus_integration/fresh-collision"
    ).exists()


def test_create_manifest_adoption_requires_existing_regular_action(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)

    with pytest.raises(IntegrationError, match="action"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            adopt_backup=legacy,
            stamp="missing-action",
        )

    assert not (
        citysample_root / "ArgusBackups/argus_integration/missing-action"
    ).exists()


def test_create_manifest_adoption_rejects_action_directory(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)
    (citysample_root / ACTION_REL).mkdir(parents=True)

    with pytest.raises(IntegrationError, match="action"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            adopt_backup=legacy,
            stamp="action-directory",
        )


def test_create_manifest_adoption_rejects_action_symlink(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)
    target = tmp_path / "outside-action.uasset"
    target.write_bytes(b"outside")
    action_path = citysample_root / ACTION_REL
    action_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        action_path.symlink_to(target)
    except OSError as exc:
        pytest.skip("symlinks unavailable: {}".format(exc))

    with pytest.raises(IntegrationError, match="action"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            adopt_backup=legacy,
            stamp="action-symlink",
        )

    assert target.read_bytes() == b"outside"


def test_create_manifest_preflights_sources_before_creating_backup(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    (citysample_root / BLUEPRINT_REL).unlink()
    backup_dir = citysample_root / "ArgusBackups/argus_integration/missing-source"

    with pytest.raises(IntegrationError, match="managed file"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            stamp="missing-source",
        )

    assert not backup_dir.exists()


def test_create_manifest_removes_new_backup_after_copy_failure(tmp_path, monkeypatch):
    citysample_root = make_citysample_tree(tmp_path)
    backup_dir = citysample_root / "ArgusBackups/argus_integration/copy-failure"

    def fail_copy(source, destination):
        raise OSError("copy failed")

    monkeypatch.setattr(
        "scripts.citysample_argus_integration.shutil.copy2", fail_copy
    )

    with pytest.raises(OSError, match="copy failed"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            stamp="copy-failure",
        )

    assert not backup_dir.exists()


def test_create_manifest_rejects_nested_stamp(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)

    with pytest.raises(IntegrationError, match="stamp"):
        create_manifest(
            citysample_root,
            tmp_path / "Argus",
            tmp_path / "UE",
            stamp="../outside",
        )

    assert not (citysample_root / "ArgusBackups/outside").exists()


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
    make_installed_files(citysample_root)
    for path in (wanted, other):
        mark_installed(path, citysample_root)

    assert find_manifest(
        tmp_path / "ArgusA", citysample_root, tmp_path / "UEA"
    ) == wanted


def test_find_manifest_skips_bad_stale_candidates(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root = tmp_path / "Argus"
    ue_root = tmp_path / "UE"
    wanted = create_manifest(
        citysample_root,
        argus_root,
        ue_root,
        stamp="wanted",
    )
    make_installed_files(citysample_root)
    mark_installed(wanted, citysample_root)
    stale = (
        citysample_root
        / "ArgusBackups/argus_integration/stale/manifest.json"
    )
    stale.parent.mkdir()
    stale.write_bytes(b"not json")

    assert find_manifest(argus_root, citysample_root, ue_root) == wanted


def test_find_manifest_explicit_bad_manifest_is_integration_error(tmp_path):
    bad_manifest = tmp_path / "bad-manifest.json"
    bad_manifest.write_bytes(b"not json")

    with pytest.raises(IntegrationError, match="manifest"):
        find_manifest(tmp_path / "Argus", tmp_path / "CitySample", tmp_path / "UE", bad_manifest)


def test_restore_rejects_live_path_traversal_before_hashing_or_mutation(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="live-traversal",
    )
    victim = tmp_path / "victim.txt"
    victim.write_bytes(b"victim")
    header_before = (citysample_root / HEADER_REL).read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../victim.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.sha256_file",
        lambda path: pytest.fail("hashing occurred before manifest validation"),
    )

    with pytest.raises(IntegrationError, match="manifest"):
        restore_manifest(manifest_path, check_drift=False)

    assert victim.read_bytes() == b"victim"
    assert (citysample_root / HEADER_REL).read_bytes() == header_before
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "installing"


def test_restore_rejects_backup_path_traversal_before_hashing_or_mutation(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="backup-traversal",
    )
    victim = manifest_path.parent.parent / "victim.txt"
    victim.write_bytes(b"victim")
    header_before = (citysample_root / HEADER_REL).read_bytes()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["backup"] = "../victim.txt"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.sha256_file",
        lambda path: pytest.fail("hashing occurred before manifest validation"),
    )

    with pytest.raises(IntegrationError, match="manifest"):
        restore_manifest(manifest_path, check_drift=False)

    assert victim.read_bytes() == b"victim"
    assert (citysample_root / HEADER_REL).read_bytes() == header_before
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "installing"


@pytest.mark.parametrize(
    "case",
    [
        "state",
        "roots",
        "backup_dir",
        "path_type",
        "duplicate",
        "existing_created",
        "existing_backup",
        "original_hash",
        "installed_hash",
        "action",
    ],
)
def test_load_manifest_rejects_invalid_contract(tmp_path, case):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="invalid-{}".format(case),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if case == "state":
        manifest["state"] = "unknown"
    elif case == "roots":
        manifest["roots"]["extra"] = str(tmp_path)
    elif case == "backup_dir":
        manifest["backup_dir"] = str(tmp_path)
    elif case == "path_type":
        manifest["files"][0]["path"] = []
    elif case == "duplicate":
        manifest["files"][-1] = dict(manifest["files"][0])
    elif case == "existing_created":
        manifest["files"][0]["created"] = True
    elif case == "existing_backup":
        manifest["files"][0]["backup"] = "files/wrong"
    elif case == "original_hash":
        manifest["files"][0]["original_sha256"] = None
    elif case == "installed_hash":
        manifest["files"][0]["installed_sha256"] = "not-a-hash"
    else:
        manifest["files"][-1]["backup"] = "files/action"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_wrong_location(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="right-location",
    )
    wrong_path = tmp_path / "manifest.json"
    shutil.copy2(manifest_path, wrong_path)

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(wrong_path)


@pytest.mark.parametrize("case", ["missing", "directory", "encoding", "json"])
def test_load_manifest_normalizes_read_failures(tmp_path, case):
    manifest_path = tmp_path / "manifest.json"
    if case == "directory":
        manifest_path.mkdir()
    elif case == "encoding":
        manifest_path.write_bytes(b"\xff")
    elif case == "json":
        manifest_path.write_text("{", encoding="utf-8")

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    "phases",
    [None, "backup", ["backup", "unknown"], ["backup", "backup"]],
)
def test_load_manifest_rejects_invalid_completed_phases(tmp_path, phases):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="invalid-phases-{}".format(type(phases).__name__),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if phases is None:
        manifest.pop("completed_phases")
    else:
        manifest["completed_phases"] = phases
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("state", "phases"),
    [
        ("installing", ["backup", "build"]),
        ("installing", FULL_INSTALL_PHASES),
        ("installed", FULL_INSTALL_PHASES + ["restore"]),
        ("installed", FULL_INSTALL_PHASES[:-1]),
        ("restoring", FULL_INSTALL_PHASES + ["restore"]),
        ("restored", FULL_INSTALL_PHASES),
    ],
)
def test_load_manifest_rejects_phase_order_or_state_mismatch(
    tmp_path, state, phases
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="phase-state-{}".format(state),
    )
    make_installed_files(citysample_root)
    manifest = mark_installed(manifest_path, citysample_root)
    manifest["state"] = state
    manifest["completed_phases"] = phases
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IntegrationError, match="completed phases"):
        load_manifest(manifest_path)


@pytest.mark.parametrize(
    ("state", "phases"),
    [
        ("restoring", FULL_INSTALL_PHASES),
        ("restoring", ["backup", "source"]),
        ("restored", ["backup", "restore"]),
        ("failed", ["backup", "source", "build"]),
        ("failed", ["backup", "source", "build", "restore"]),
        ("failed", FULL_INSTALL_PHASES),
        ("failed", FULL_INSTALL_PHASES + ["restore"]),
    ],
)
def test_load_manifest_accepts_real_restore_and_failure_sequences(
    tmp_path, state, phases
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="valid-phase-{}-{}".format(state, len(phases)),
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["state"] = state
    manifest["completed_phases"] = phases
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert load_manifest(manifest_path)[1]["state"] == state


def test_load_manifest_requires_installed_hashes_for_installed_state(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="installed-hashes",
    )
    make_installed_files(citysample_root)
    manifest = mark_installed(manifest_path, citysample_root)
    manifest["files"][0]["installed_sha256"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(manifest_path)


def test_load_manifest_rejects_live_symlink_escape_when_supported(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="symlink-escape",
    )
    victim = tmp_path / "outside.txt"
    victim.write_bytes(b"outside")
    header_path = citysample_root / HEADER_REL
    header_path.unlink()
    try:
        header_path.symlink_to(victim)
    except OSError as exc:
        pytest.skip("symlinks unavailable: {}".format(exc))

    with pytest.raises(IntegrationError, match="manifest"):
        load_manifest(manifest_path)

    assert victim.read_bytes() == b"outside"


def test_restore_manifest_rejects_installed_drift(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        Path(r"D:\Portable\Argus"),
        Path(r"E:\UE_5.8"),
        commit="abc123",
        stamp="20260725_010203",
    )
    make_installed_files(citysample_root)
    mark_installed(manifest_path, citysample_root)
    (citysample_root / HEADER_REL).write_text("user change", encoding="utf-8")

    with pytest.raises(IntegrationError, match="drift"):
        restore_manifest(manifest_path)


def test_restore_requires_installed_state_before_mutation(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="not-installed",
    )
    live_before = managed_bytes(citysample_root)
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(IntegrationError, match="installed"):
        restore_manifest(manifest_path)

    assert managed_bytes(citysample_root) == live_before
    assert manifest_path.read_bytes() == manifest_before


@pytest.mark.parametrize("failure", ["corrupt", "missing"])
def test_restore_preflights_all_backups_before_mutation(tmp_path, failure):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="backup-{}".format(failure),
    )
    make_installed_files(citysample_root)
    manifest = mark_installed(manifest_path, citysample_root)
    row = manifest["files"][0 if failure == "corrupt" else -2]
    backup_path = manifest_path.parent / row["backup"]
    if failure == "corrupt":
        backup_path.write_bytes(b"corrupt")
    else:
        backup_path.unlink()
    live_before = managed_bytes(citysample_root)
    manifest_before = manifest_path.read_bytes()

    with pytest.raises(IntegrationError, match="backup"):
        restore_manifest(manifest_path)

    assert managed_bytes(citysample_root) == live_before
    assert manifest_path.read_bytes() == manifest_before


def test_restore_uses_atomic_replacements_and_deletes_action_last(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="atomic-restore",
    )
    action_path = make_installed_files(citysample_root)
    mark_installed(manifest_path, citysample_root)
    sentinel = manifest_path.with_name(manifest_path.name + ".argus.tmp")
    sentinel.write_bytes(b"keep")
    live_paths = {citysample_root / relative for relative in EXISTING_FILES}
    replacements = []
    real_replace = os.replace

    def record_replace(source, destination):
        source = Path(source)
        destination = Path(destination)
        if destination in live_paths:
            assert action_path.is_file()
            replacements.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(
        "scripts.citysample_argus_integration.os.replace", record_replace
    )

    restore_manifest(manifest_path)

    assert {destination for _, destination in replacements} == live_paths
    assert all(source.parent == destination.parent for source, destination in replacements)
    assert all(
        source != destination.with_name(destination.name + ".argus.tmp")
        for source, destination in replacements
    )
    assert not action_path.exists()
    assert sentinel.read_bytes() == b"keep"


def test_restore_cleans_temp_and_keeps_action_if_replace_fails(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    manifest_path = create_manifest(
        citysample_root,
        tmp_path / "Argus",
        tmp_path / "UE",
        stamp="replace-failure",
    )
    action_path = make_installed_files(citysample_root)
    mark_installed(manifest_path, citysample_root)
    header_path = citysample_root / HEADER_REL
    parent_before = set(header_path.parent.iterdir())
    real_replace = os.replace

    def fail_header_replace(source, destination):
        if Path(destination) == header_path:
            raise OSError("replace failed")
        real_replace(source, destination)

    monkeypatch.setattr(
        "scripts.citysample_argus_integration.os.replace", fail_header_replace
    )

    with pytest.raises(OSError, match="replace failed"):
        restore_manifest(manifest_path)

    assert set(header_path.parent.iterdir()) == parent_before
    assert action_path.read_bytes() == b"installed-action"
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "restoring"


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
    manifest["completed_phases"] = FULL_INSTALL_PHASES.copy()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restore_manifest(manifest_path)

    assert (citysample_root / HEADER_REL).read_bytes() == original_header
    assert not action_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "restored"


def test_restore_integration_finalizes_only_after_successful_build(
    tmp_path, monkeypatch
):
    manifest_path = make_installed_integration(tmp_path)
    states_during_build = []

    def fake_run(command):
        states_during_build.append(load_manifest(manifest_path)[1]["state"])

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fake_run)

    assert restore_integration(manifest_path) == manifest_path
    _, manifest = load_manifest(manifest_path)
    assert states_during_build == ["restoring"]
    assert manifest["state"] == "restored"
    assert manifest["completed_phases"] == FULL_INSTALL_PHASES + ["restore"]


def test_restore_integration_build_failure_never_records_restored(
    tmp_path, monkeypatch
):
    manifest_path = make_installed_integration(tmp_path)

    def fail_build(command):
        assert load_manifest(manifest_path)[1]["state"] == "restoring"
        raise IntegrationError("rebuild failed")

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fail_build)

    with pytest.raises(IntegrationError, match="rebuild failed"):
        restore_integration(manifest_path)

    _, manifest = load_manifest(manifest_path)
    assert manifest["state"] == "failed"
    assert "restore" not in manifest["completed_phases"]


def test_restore_integration_interrupt_leaves_restoring(tmp_path, monkeypatch):
    manifest_path = make_installed_integration(tmp_path)

    def interrupt_build(command):
        assert load_manifest(manifest_path)[1]["state"] == "restoring"
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "scripts.citysample_argus_integration._run", interrupt_build
    )

    with pytest.raises(KeyboardInterrupt):
        restore_integration(manifest_path)

    _, manifest = load_manifest(manifest_path)
    assert manifest["state"] == "restoring"
    assert "restore" not in manifest["completed_phases"]


@pytest.mark.parametrize("record_failure", ["load", "write"])
def test_restore_integration_re_raises_build_error_when_recording_fails(
    tmp_path, monkeypatch, record_failure
):
    manifest_path = make_installed_integration(tmp_path)
    original = IntegrationError("original rebuild failure")

    def fail_record(*args, **kwargs):
        raise IntegrationError("manifest recording failed")

    def fail_build(command):
        monkeypatch.setattr(
            "scripts.citysample_argus_integration.{}".format(
                "load_manifest"
                if record_failure == "load"
                else "_write_json_atomic"
            ),
            fail_record,
        )
        raise original

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fail_build)

    with pytest.raises(IntegrationError) as caught:
        restore_integration(manifest_path)

    assert caught.value is original


def test_restore_rejects_unsafe_build_path_before_restoring(tmp_path, monkeypatch):
    manifest_path = make_installed_integration(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["roots"]["ue"] = str(tmp_path / "UE&unsafe")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    mutations = []
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.restore_manifest",
        lambda *args, **kwargs: mutations.append("restore_manifest"),
    )

    with pytest.raises(IntegrationError, match="unsafe cmd metacharacter.*path"):
        restore_integration(manifest_path)

    assert mutations == []
    assert load_manifest(manifest_path)[1]["state"] == "installed"


def test_tasklist_detection_is_case_insensitive_and_ignores_info_text():
    assert _tasklist_has_unreal_editor(
        '"UNREALEDITOR.EXE","52508","Console","1","2,048 K"'
    )
    assert _tasklist_has_unreal_editor(
        '"unrealeditor-CMD.exe","52509","Console","1","2,048 K"'
    )
    assert not _tasklist_has_unreal_editor(
        "INFO: No tasks are running which match the specified criteria."
    )
    assert not _tasklist_has_unreal_editor("信息: 没有运行的任务与指定标准匹配。")


def test_build_command_uses_parameterized_roots(tmp_path):
    ue_root = tmp_path / "UE & Root's (x86)"
    citysample_root = tmp_path / "City & Sample's (Demo)"

    assert build_command(ue_root, citysample_root) == [
        str(ue_root / "Engine/Build/BatchFiles/Build.bat"),
        "CitySampleEditor",
        "Win64",
        "Development",
        "-Project={}".format(citysample_root / "CitySample.uproject"),
        "-WaitMutex",
        "-FromMsBuild",
    ]


@pytest.mark.parametrize("root_name", ["ue_root", "citysample_root"])
@pytest.mark.parametrize("character", list('%!"\r\n'))
def test_build_command_always_rejects_expanding_cmd_metacharacters(
    tmp_path, root_name, character
):
    roots = {
        "ue_root": tmp_path / "UE Root",
        "citysample_root": tmp_path / "CitySample Root",
    }
    roots[root_name] = Path("{}{}unsafe".format(roots[root_name], character))

    with pytest.raises(IntegrationError, match="unsafe cmd metacharacter.*path"):
        build_command(roots["ue_root"], roots["citysample_root"])


@pytest.mark.parametrize("root_name", ["ue_root", "citysample_root"])
@pytest.mark.parametrize("character", list("&|<>^()"))
def test_build_command_rejects_unquoted_cmd_metacharacters(root_name, character):
    roots = {
        "ue_root": Path("C:/UE"),
        "citysample_root": Path("C:/CitySample"),
    }
    roots[root_name] = Path("{}{}unsafe".format(roots[root_name], character))

    with pytest.raises(IntegrationError, match="unsafe cmd metacharacter.*path"):
        build_command(roots["ue_root"], roots["citysample_root"])


@pytest.mark.parametrize("root_name", ["ue_root", "citysample_root"])
@pytest.mark.parametrize("character", list("&|<>^()"))
@pytest.mark.parametrize("whitespace", [" ", "\t"])
def test_build_command_allows_quoted_cmd_metacharacters(
    root_name, character, whitespace
):
    roots = {
        "ue_root": Path("C:/UE"),
        "citysample_root": Path("C:/CitySample"),
    }
    roots[root_name] = Path(
        "{}{}Root{}safe".format(roots[root_name], whitespace, character)
    )

    command = build_command(roots["ue_root"], roots["citysample_root"])

    checked_argument = command[0] if root_name == "ue_root" else command[4]
    assert subprocess.list2cmdline([checked_argument]).startswith('"')


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe batch parsing")
def test_build_command_runs_quoted_cmd_metacharacters_without_injection(tmp_path):
    ue_root = tmp_path / "UE & Tools (x86)"
    build_bat = ue_root / "Engine/Build/BatchFiles/Build.bat"
    build_bat.parent.mkdir(parents=True)
    received_project = tmp_path / "received-project.txt"
    injection_marker = tmp_path / "unexpected-command.txt"
    assert " " not in str(injection_marker)
    citysample_root = Path(
        "{} & echo.UNEXPECTED>{} & rem.(Demo)".format(
            tmp_path / "City", injection_marker
        )
    )
    build_bat.write_bytes(
        (
            "@echo off\r\n"
            'set "project=%~4"\r\n'
            '> "{}" <nul set /p "=%project%"\r\n'
            "exit /b 0\r\n".format(received_project)
        ).encode("utf-8")
    )
    raw_project_argument = "-Project={}".format(
        citysample_root / "CitySample.uproject"
    )
    raw_command = [
        str(build_bat),
        "CitySampleEditor",
        "Win64",
        "Development",
        raw_project_argument,
        "-WaitMutex",
        "-FromMsBuild",
    ]

    assert subprocess.list2cmdline([raw_command[0]]).startswith('"')
    assert subprocess.list2cmdline([raw_command[4]]).startswith('"')
    _run(raw_command)
    assert received_project.read_text(encoding="utf-8") == raw_project_argument
    assert not injection_marker.exists()

    received_project.unlink()
    _run(build_command(ue_root, citysample_root))
    assert received_project.read_text(encoding="utf-8") == raw_project_argument
    assert not injection_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="requires Windows cmd.exe batch parsing")
def test_build_command_rejects_cmd_injection_before_batch_execution(tmp_path):
    build_bat = tmp_path / "UE/Engine/Build/BatchFiles/Build.bat"
    build_bat.parent.mkdir(parents=True)
    batch_marker = tmp_path / "batch-ran.txt"
    injection_marker = tmp_path / "injected.txt"
    build_bat.write_text(
        '@echo off\r\n> "{}" echo batch-ran\r\n'.format(batch_marker),
        encoding="utf-8",
    )
    unsafe_citysample_root = Path(
        "{}&echo.INJECTED>{}&rem.".format(tmp_path / "Project", injection_marker)
    )
    raw_project_argument = "-Project={}".format(
        unsafe_citysample_root / "CitySample.uproject"
    )
    raw_command = [
        str(build_bat),
        "CitySampleEditor",
        "Win64",
        "Development",
        raw_project_argument,
        "-WaitMutex",
        "-FromMsBuild",
    ]

    assert " " not in raw_project_argument
    _run(raw_command)
    assert batch_marker.read_text(encoding="utf-8").strip() == "batch-ran"
    assert injection_marker.read_text(encoding="utf-8").strip() == "INJECTED"

    batch_marker.unlink()
    injection_marker.unlink()

    with pytest.raises(IntegrationError, match="unsafe cmd metacharacter.*path"):
        _run(build_command(tmp_path / "UE", unsafe_citysample_root))

    assert not batch_marker.exists()
    assert not injection_marker.exists()


def test_asset_command_survives_windows_and_ue_command_line_reconstruction(tmp_path):
    ue_root = tmp_path / "UE Root"
    citysample_root = tmp_path / "CitySample Root"
    argus_root = tmp_path / "Argus Root"
    result_path = tmp_path / "Result Root/result.json"

    command = asset_command(
        ue_root, citysample_root, argus_root, "verify", result_path
    )

    assert command[:2] == [
        str(ue_root / "Engine/Binaries/Win64/UnrealEditor-Cmd.exe"),
        str(citysample_root / "CitySample.uproject"),
    ]
    assert command[-2:] == ["-unattended", "-nop4"]
    reconstructed = reconstruct_ue_windows_command(command)
    execute = reconstructed[reconstructed.index("-ExecutePythonScript=") :].split(
        " -unattended", 1
    )[0]
    assert execute == '-ExecutePythonScript="{} verify --result \\"{}\\""'.format(
        (argus_root / "scripts/citysample_argus_assets.py").as_posix(),
        result_path.as_posix(),
    )
    assert not execute.startswith('-ExecutePythonScript=""')


@pytest.mark.parametrize("payload", [b"not json", b"\xff"])
def test_asset_result_rejects_invalid_json_as_integration_error(tmp_path, payload):
    result_path = tmp_path / "asset_result.json"
    result_path.write_bytes(payload)

    with pytest.raises(IntegrationError, match="invalid"):
        _require_asset_result(result_path)


def test_run_assets_uses_unique_result_paths_and_cleans_them(tmp_path, monkeypatch):
    manifest_path = tmp_path / "backup/manifest.json"
    manifest_path.parent.mkdir()
    result_paths = []

    def fake_run(command):
        result_path = asset_result_path(command)
        assert not result_path.exists()
        result_paths.append(result_path)
        result_path.write_text('{"ok": true}\n', encoding="utf-8")

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fake_run)

    for _ in range(2):
        assert _run_assets(
            tmp_path / "Argus",
            tmp_path / "CitySample",
            tmp_path / "UE",
            "verify",
            manifest_path,
        ) == {"ok": True}

    assert result_paths[0] != result_paths[1]
    assert all(path.parent == manifest_path.parent for path in result_paths)
    assert not any(path.exists() for path in result_paths)


@pytest.mark.parametrize(
    ("completed", "message"),
    [
        (SimpleNamespace(returncode=1, stdout=""), "tasklist failed"),
        (
            SimpleNamespace(
                returncode=0,
                stdout='"UnrealEditor.exe","52508","Console","1","2,048 K"',
            ),
            "close UnrealEditor",
        ),
        (
            SimpleNamespace(
                returncode=0,
                stdout='"UnrealEditor-Cmd.exe","52509","Console","1","2,048 K"',
            ),
            "close UnrealEditor",
        ),
    ],
)
def test_require_editor_closed_rejects_check_failure_or_running_editor(
    monkeypatch, completed, message
):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return completed

    monkeypatch.setattr(
        "scripts.citysample_argus_integration.subprocess.run", fake_run
    )

    with pytest.raises(IntegrationError, match=message):
        require_editor_closed()

    assert calls == [
        (
            ["tasklist", "/FO", "CSV", "/NH"],
            {"check": False, "capture_output": True, "text": True},
        )
    ]


def test_integration_lock_rejects_cross_process_competition(tmp_path):
    citysample_root = tmp_path / "CitySample"
    citysample_root.mkdir()

    with _integration_lock(citysample_root):
        completed = try_integration_lock_in_subprocess(citysample_root)

    assert completed.returncode == 23
    assert "lock" in completed.stdout.casefold()


def test_integration_lock_releases_for_next_process(tmp_path):
    citysample_root = tmp_path / "CitySample"
    citysample_root.mkdir()

    with _integration_lock(citysample_root):
        pass

    assert try_integration_lock_in_subprocess(citysample_root).returncode == 0


def test_integration_lock_open_failure_is_integration_error(tmp_path):
    citysample_root = tmp_path / "CitySample"
    citysample_root.write_text("not a directory", encoding="utf-8")

    with pytest.raises(IntegrationError, match="lock"):
        with _integration_lock(citysample_root):
            pass


def test_main_checks_editor_before_starting_integration(tmp_path, monkeypatch):
    citysample_root = tmp_path / "CitySample"
    citysample_root.mkdir()
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.require_editor_closed",
        lambda: (_ for _ in ()).throw(IntegrationError("editor open")),
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.install_integration",
        lambda *args, **kwargs: pytest.fail("integration started"),
    )

    with pytest.raises(IntegrationError, match="editor open"):
        main(["install", "--citysample-root", str(citysample_root)])


def test_main_uses_explicit_manifest_citysample_root_and_reloads_inside_lock(
    tmp_path, monkeypatch
):
    manifest_path = make_installed_integration(tmp_path)
    expected_root = Path(load_manifest(manifest_path)[1]["roots"]["citysample"])
    real_load_manifest = load_manifest
    locked = False
    lock_roots = []
    load_lock_states = []

    @contextmanager
    def fake_lock(citysample_root):
        nonlocal locked
        lock_roots.append(Path(citysample_root).resolve())
        locked = True
        try:
            yield
        finally:
            locked = False

    def record_load(path):
        load_lock_states.append(locked)
        return real_load_manifest(path)

    def check_editor():
        assert locked

    def verify(manifest):
        assert locked
        return manifest

    monkeypatch.setattr(
        "scripts.citysample_argus_integration._integration_lock", fake_lock
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.load_manifest", record_load
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.require_editor_closed", check_editor
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.verify_integration", verify
    )

    assert main(["verify", "--manifest", str(manifest_path)]) == 0
    assert lock_roots == [expected_root.resolve()]
    assert load_lock_states[:2] == [False, True]


def test_adopt_install_builds_and_verifies_without_rewriting_sources(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = read_source_files(citysample_root)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)
    patched = patch_source_texts(
        original[0][0], original[1][0], original[2][0], argus_root
    )
    write_source_files(citysample_root, patched, original)
    expected_sources = tuple(
        (text, format_[1], format_[2])
        for text, format_ in zip(patched, original)
    )
    action = citysample_root / ACTION_REL
    action.parent.mkdir(parents=True, exist_ok=True)
    action.write_bytes(b"action")
    calls = []
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._run", calls.append
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

    assert read_source_files(citysample_root) == expected_sources
    assert len(calls) == 2
    assert calls[0] == build_command(ue_root, citysample_root)
    assert " verify " in calls[1][2]
    result_path = asset_result_path(calls[1])
    assert result_path.parent == manifest_path.parent
    assert not result_path.exists()
    _, manifest = load_manifest(manifest_path)
    assert manifest["state"] == "installed"
    assert manifest["completed_phases"] == [
        "backup",
        "source",
        "build",
        "assets",
        "verify",
    ]
    assert len(manifest["completed_phases"]) == len(
        set(manifest["completed_phases"])
    )
    assert all(len(row["installed_sha256"]) == 64 for row in manifest["files"])


def test_existing_install_without_adoption_runs_explicit_verify(
    tmp_path, monkeypatch
):
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
    manifest_path = create_manifest(
        citysample_root,
        argus_root,
        ue_root,
        adopt_backup=legacy,
        commit="abc123",
        stamp="installed",
    )
    mark_installed(manifest_path, citysample_root)
    calls = []
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._run", calls.append
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._require_asset_result",
        lambda path: {"ok": True},
    )

    assert install_integration(argus_root, citysample_root, ue_root) == manifest_path
    assert len(calls) == 1
    assert " verify " in calls[0][2]


def test_install_rejects_partial_source_before_backup_or_process(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = read_source_files(citysample_root)
    patched = patch_source_texts(
        original[0][0], original[1][0], original[2][0], argus_root
    )
    write_source_files(
        citysample_root,
        (patched[0], original[1][0], original[2][0]),
        original,
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._run",
        lambda command: pytest.fail("process started"),
    )

    with pytest.raises(IntegrationError, match="partial or conflicting"):
        install_integration(argus_root, citysample_root, ue_root, commit="abc123")

    assert not (citysample_root / "ArgusBackups/argus_integration").exists()


def test_clean_install_rejects_adopt_backup_before_creating_manifest(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    legacy = make_legacy_backup(tmp_path / "legacy", citysample_root)

    with pytest.raises(IntegrationError, match="required only"):
        install_integration(
            argus_root,
            citysample_root,
            ue_root,
            adopt_backup=legacy,
            commit="abc123",
        )

    assert not (citysample_root / "ArgusBackups/argus_integration").exists()


def test_install_rejects_unsafe_build_path_before_manifest_or_source_write(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    unsafe_ue_root = ue_root.with_name("UE&unsafe")
    ue_root.rename(unsafe_ue_root)
    mutations = []

    def record_create_manifest(*args, **kwargs):
        mutations.append("create_manifest")
        return tmp_path / "manifest.json"

    monkeypatch.setattr(
        "scripts.citysample_argus_integration.create_manifest",
        record_create_manifest,
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.write_source_files",
        lambda *args, **kwargs: mutations.append("write_source_files"),
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration._complete_phase",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "scripts.citysample_argus_integration.restore_manifest",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(IntegrationError, match="unsafe cmd metacharacter.*path"):
        install_integration(
            argus_root,
            citysample_root,
            unsafe_ue_root,
            commit="abc123",
        )

    assert mutations == []


def test_fresh_install_rolls_back_and_records_completed_stages_on_asset_failure(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = {
        relative: (citysample_root / relative).read_bytes()
        for relative in EXISTING_FILES
    }
    calls = []

    def fake_run(command):
        calls.append(command)
        if any(part.startswith("-ExecutePythonScript=") for part in command):
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

    assert {
        relative: (citysample_root / relative).read_bytes()
        for relative in EXISTING_FILES
    } == original
    assert not (citysample_root / ACTION_REL).exists()
    manifest_path = (
        citysample_root
        / "ArgusBackups/argus_integration/20260725_010203/manifest.json"
    )
    _, manifest = load_manifest(manifest_path)
    assert manifest["state"] == "failed"
    assert manifest["error"] == "asset stage failed"
    assert manifest["rollback_completed"] is True
    assert manifest["completed_phases"] == [
        "backup",
        "source",
        "build",
        "restore",
    ]
    assert len(calls) == 3
    assert calls[0] == calls[2] == build_command(ue_root, citysample_root)
    assert " install " in calls[1][2]
    result_path = asset_result_path(calls[1])
    assert result_path.parent == manifest_path.parent
    assert not result_path.exists()


def test_install_rollback_interrupt_leaves_manifest_restoring(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    originals = managed_bytes(citysample_root)
    builds = 0

    def fake_run(command):
        nonlocal builds
        if any(part.startswith("-ExecutePythonScript=") for part in command):
            raise IntegrationError("asset failed")
        builds += 1
        if builds == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fake_run)

    with pytest.raises(KeyboardInterrupt):
        install_integration(
            argus_root,
            citysample_root,
            ue_root,
            commit="abc123",
            stamp="rollback-interrupt",
        )

    assert managed_bytes(citysample_root) == originals
    manifest_path = (
        citysample_root
        / "ArgusBackups/argus_integration/rollback-interrupt/manifest.json"
    )
    _, manifest = load_manifest(manifest_path)
    assert manifest["state"] == "restoring"
    assert "restore" not in manifest["completed_phases"]


def test_install_re_raises_original_when_rollback_and_recording_fail(
    tmp_path, monkeypatch
):
    citysample_root = make_citysample_tree(tmp_path)
    argus_root, ue_root = make_tool_roots(tmp_path, citysample_root)
    original = IntegrationError("original asset failure")

    def fail_rollback(*args, **kwargs):
        raise IntegrationError("rollback failed")

    def fail_record(*args, **kwargs):
        raise IntegrationError("manifest recording failed")

    def fake_run(command):
        if any(part.startswith("-ExecutePythonScript=") for part in command):
            monkeypatch.setattr(
                "scripts.citysample_argus_integration.restore_manifest",
                fail_rollback,
            )
            monkeypatch.setattr(
                "scripts.citysample_argus_integration.load_manifest", fail_record
            )
            raise original

    monkeypatch.setattr("scripts.citysample_argus_integration._run", fake_run)

    with pytest.raises(IntegrationError) as caught:
        install_integration(
            argus_root,
            citysample_root,
            ue_root,
            commit="abc123",
            stamp="preserve-original",
        )

    assert caught.value is original
