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
    assert '''#if WITH_EDITOR
		if (CaptureAction)
		{
			EnhancedInputComponent->BindAction(CaptureAction, ETriggerEvent::Started, this, &ThisClass::CaptureActionBinding);
		}
#endif''' in source
    assert '''void UPhotoModeComponent::CaptureActionBinding()
{
#if WITH_EDITOR
	if (State != EPhotoModeState::Active)
	{
		return;
	}

	IPythonScriptPlugin* const PythonPlugin = IPythonScriptPlugin::Get();
	if (!PythonPlugin)
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture failed: PythonScriptPlugin is unavailable."));
		return;
	}

	if (!PythonPlugin->IsPythonInitialized())
	{
		PythonPlugin->ForceEnablePythonAtRuntime();
	}

	if (!PythonPlugin->IsPythonInitialized())
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture failed: Python is not initialized."));
		return;
	}

	static const TCHAR* const Command = TEXT(
		"import sys; "
		"p=r'D:/Study/Code/Python/UE/cv/Argus/scripts'; "
		"sys.path.insert(0, p) if p not in sys.path else None; "
		"import capture_player_view; "
		"capture_player_view.capture_player_view()"
	);

	if (!PythonPlugin->ExecPythonCommand(Command))
	{
		UE_LOG(LogCitySamplePhotoMode, Error, TEXT("Argus capture Python command failed."));
	}
#endif
}''' in source
    assert build.count('PrivateDependencyModuleNames.Add("PythonScriptPlugin")') == 1
    assert '''if (Target.bBuildEditor == true)
		{
			PrivateDependencyModuleNames.Add("PythonScriptPlugin");

			PublicDependencyModuleNames.AddRange(''' in build
