"""Lazy public service exports used by Argus entry scripts."""

from importlib import import_module


_EXPORTS = {
    "AnnotationController": ("annotation_control", "AnnotationController"),
    "CaptureService": ("capture_system", "CaptureService"),
    "DataPipelineService": ("data_pipeline", "DataPipelineService"),
    "DualCaptureSetupService": ("capture_system", "DualCaptureSetupService"),
    "RuntimeCaptureController": ("runtime_control", "RuntimeCaptureController"),
    "RuntimePlaySessionController": (
        "runtime_session",
        "RuntimePlaySessionController",
    ),
    "RuntimeSemanticStencilController": (
        "runtime_semantics",
        "RuntimeSemanticStencilController",
    ),
    "SceneObjectCatalog": ("scene_objects", "SceneObjectCatalog"),
    "SemanticPostProcessBuilder": ("post_process", "SemanticPostProcessBuilder"),
    "SemanticRuleBuilder": ("annotation_control", "SemanticRuleBuilder"),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    try:
        module_name, attribute_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc

    value = getattr(import_module(".{}".format(module_name), __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))
