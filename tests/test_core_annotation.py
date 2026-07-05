from argus_core.model import AnnotationRule, RenderPolicy, TargetType


def test_legacy_booleans_normalize_to_visible_labeled_policy():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Road_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "road",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "2",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.target.target_type == TargetType.COMPONENT
    assert rule.render_policy == RenderPolicy.VISIBLE_LABELED
    assert rule.effective_stencil == 2


def test_material_slot_target_is_detected_from_slot_filter():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Building_01",
            "component_name": "StaticMeshComponent0",
            "material_slot": "Window",
            "semantic_class": "glass",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "12",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.target.target_type == TargetType.MATERIAL_SLOT
    assert rule.target.material_slot == "Window"


def test_missing_mask_stencil_uses_unknown_stencil():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Unknown_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "unknown",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.effective_stencil == 250


def test_ignore_class_uses_ignore_stencil_when_labeled():
    rule = AnnotationRule.from_legacy_row(
        {
            "actor_name": "Ignore_01",
            "component_name": "StaticMeshComponent0",
            "semantic_class": "ignore",
            "render_main_pass": "true",
            "render_custom_depth": "true",
            "stencil": "99",
        },
        unknown_stencil=250,
        ignore_stencil=254,
    )

    assert rule.effective_stencil == 254
