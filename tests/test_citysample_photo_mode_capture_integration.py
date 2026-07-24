from pathlib import Path
import pytest

CITYSAMPLE_ROOT = Path(r"E:\UnrealProject\CitySample")
pytestmark = pytest.mark.skipif(
    not CITYSAMPLE_ROOT.exists(),
    reason="CitySample local integration is not installed",
)

def test_photo_mode_source_contains_argus_capture_binding():
    header = (CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.h").read_text(encoding="utf-8-sig")
    source = (CITYSAMPLE_ROOT / "Source/CitySample/Camera/PhotoModeComponent.cpp").read_text(encoding="utf-8-sig")
    build = (CITYSAMPLE_ROOT / "Source/CitySample/CitySample.Build.cs").read_text(encoding="utf-8-sig")
    assert "UInputAction* CaptureAction" in header
    assert "void CaptureActionBinding();" in header
    assert '''#if WITH_EDITOR
#include "IPythonScriptPlugin.h"
#endif''' in source

    binding_anchor = source.index("if (CaptureAction)")
    binding_start = source.rfind("#if WITH_EDITOR", 0, binding_anchor)
    binding_end = source.index("#endif", binding_anchor) + len("#endif")
    binding_block = source[binding_start:binding_end]
    assert binding_block.startswith("#if WITH_EDITOR")
    assert binding_block.count("#if WITH_EDITOR") == 1
    assert binding_block.count("#endif") == 1
    assert "if (CaptureAction)" in binding_block
    assert "BindAction(CaptureAction, ETriggerEvent::Started" in binding_block
    assert binding_block.endswith("#endif")

    handler_signature = "void UPhotoModeComponent::CaptureActionBinding()"
    handler_start = source.index(handler_signature)
    handler_end = source.index("\n#endif\n}", handler_start) + len("\n#endif\n}")
    handler = source[handler_start:handler_end]
    assert handler.startswith(handler_signature + "\n{\n#if WITH_EDITOR")
    assert handler.endswith("#endif\n}")
    assert "State != EPhotoModeState::Active" in handler
    assert "IPythonScriptPlugin::Get()" in handler
    assert "ForceEnablePythonAtRuntime()" in handler
    assert "IsPythonInitialized()" in handler
    assert "capture_player_view.capture_player_view()" in handler
    assert "ExecPythonCommand(Command)" in handler
    assert "Argus capture failed: PythonScriptPlugin is unavailable." in handler
    assert "Argus capture failed: Python is not initialized." in handler
    assert "Argus capture Python command failed." in handler

    editor_start = build.index("if (Target.bBuildEditor == true)\n\t\t{")
    editor_end = build.index("\n\t\t}", editor_start) + len("\n\t\t}")
    editor_block = build[editor_start:editor_end]
    assert build.count('PrivateDependencyModuleNames.Add("PythonScriptPlugin")') == 1
    assert 'PrivateDependencyModuleNames.Add("PythonScriptPlugin")' in editor_block
