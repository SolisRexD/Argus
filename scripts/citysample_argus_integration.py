"""Install, verify, or restore the Argus CitySample integration."""

from __future__ import annotations

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
}""".replace("{scripts_path}", scripts_path)
    build_dependency = "\n\t\t\tPrivateDependencyModuleNames.Add(\"PythonScriptPlugin\");\n"
    return {
        "header": (capture_property, capture_declaration),
        "source": (python_include, capture_binding, capture_handler),
        "build": (build_dependency,),
    }


def patch_source_texts(header, source, build, argus_root):
    fragments = expected_source_fragments(argus_root)
    texts = {"header": header, "source": source, "build": build}
    counts = [
        texts[name].count(fragment)
        for name, expected in fragments.items()
        for fragment in expected
    ]
    if any(count > 1 for count in counts):
        raise IntegrationError("source contract is duplicated")
    if any(counts) and not all(count == 1 for count in counts):
        raise IntegrationError("source contract is partial or conflicting")
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
        "IPythonScriptPlugin.h",
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
