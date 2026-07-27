import ast
import codecs
import json
import os
import shutil
from pathlib import Path

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
    create_manifest,
    expected_source_fragments,
    find_manifest,
    load_manifest,
    patch_source_texts,
    read_source_files,
    restore_manifest,
    sha256_file,
    verify_source_texts,
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


def mark_installed(manifest_path, citysample_root):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        path = citysample_root / row["path"]
        row["installed_sha256"] = sha256_file(path) if path.is_file() else None
    manifest["state"] = "installed"
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
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    restore_manifest(manifest_path)

    assert (citysample_root / HEADER_REL).read_bytes() == original_header
    assert not action_path.exists()
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["state"] == "restored"
