from pathlib import Path

import pytest

from scripts.citysample_argus_integration import verify_source_texts

CITYSAMPLE_ROOT = Path(r"E:\UnrealProject\CitySample")
pytestmark = pytest.mark.skipif(
    not CITYSAMPLE_ROOT.exists(),
    reason="CitySample local integration is not installed",
)


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
