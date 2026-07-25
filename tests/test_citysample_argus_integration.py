import ast
from pathlib import Path

import pytest

from scripts.citysample_argus_integration import (
    IntegrationError,
    expected_source_fragments,
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
