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
    assert "BindAction(CaptureAction, ETriggerEvent::Started" in source
    assert "IPythonScriptPlugin" in source
    assert "capture_player_view.capture_player_view()" in source
    editor_block = build[build.index("if (Target.bBuildEditor == true)") :]
    assert 'PrivateDependencyModuleNames.Add("PythonScriptPlugin")' in editor_block
