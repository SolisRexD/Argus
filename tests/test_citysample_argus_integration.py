import ast
import codecs
import json
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
    MAPPING_REL,
    SOURCE_REL,
    create_manifest,
    expected_source_fragments,
    find_manifest,
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


def test_create_manifest_copies_only_managed_backups(tmp_path):
    citysample_root = make_citysample_tree(tmp_path)
    legacy = tmp_path / "legacy"
    legacy_paths = {
        HEADER_REL: Path("Source/PhotoModeComponent.h"),
        SOURCE_REL: Path("Source/PhotoModeComponent.cpp"),
        BUILD_REL: Path("Source/CitySample.Build.cs"),
        MAPPING_REL: Path("Content/IM_PM_Simple_MappingContext.uasset"),
        BLUEPRINT_REL: Path("Content/BP_PhotoModeComponent.uasset"),
    }
    assert LEGACY_BACKUP_PATHS == legacy_paths
    for legacy_relative in legacy_paths.values():
        destination = legacy / legacy_relative
        destination.parent.mkdir(parents=True, exist_ok=True)
    (legacy / "UnexpectedSaves").mkdir()
    for relative, legacy_relative in legacy_paths.items():
        shutil.copy2(citysample_root / relative, legacy / legacy_relative)
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
