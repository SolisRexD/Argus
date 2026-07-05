from argus_core.model import AnnotationRule
from argus_core.planning import BackendCapabilities, StrategyKind, choose_strategy


def _rule(row):
    base = {
        "actor_name": "A",
        "component_name": "C",
        "semantic_class": "road",
        "render_main_pass": "true",
        "render_custom_depth": "true",
        "stencil": "2",
    }
    base.update(row)
    return AnnotationRule.from_legacy_row(base, unknown_stencil=250, ignore_stencil=254)


def test_component_target_uses_ue_component_stencil_when_supported():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({}), caps)

    assert decision.kind == StrategyKind.UE_COMPONENT_STENCIL
    assert decision.executable is True


def test_material_slot_target_requires_material_split_for_current_ue_backend():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({"material_slot": "Glass"}), caps)

    assert decision.kind == StrategyKind.UE_REQUIRES_MATERIAL_SPLIT
    assert decision.executable is False
    assert "material slot" in decision.reason.lower()


def test_instance_target_requires_instance_split_for_current_ue_backend():
    caps = BackendCapabilities.ue_default()
    decision = choose_strategy(_rule({"instance_index": "3"}), caps)

    assert decision.kind == StrategyKind.UE_REQUIRES_INSTANCE_SPLIT
    assert decision.executable is False
