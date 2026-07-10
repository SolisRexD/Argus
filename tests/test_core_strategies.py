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


def test_component_target_uses_direct_component_strategy_for_any_capable_backend():
    caps = BackendCapabilities(name="test", component_labeling=True)

    decision = choose_strategy(_rule({}), caps)

    assert decision.kind == StrategyKind.DIRECT_COMPONENT
    assert decision.executable is True
    assert decision.backend == "test"


def test_material_slot_target_is_deferred_when_backend_lacks_direct_support():
    caps = BackendCapabilities(name="test", component_labeling=True)

    decision = choose_strategy(
        _rule({"target_type": "material_slot", "material_slot": "Glass"}),
        caps,
    )

    assert decision.kind == StrategyKind.REQUIRES_MATERIAL_SPLIT
    assert decision.executable is False
    assert "material slot" in decision.reason.lower()


def test_material_slot_target_is_direct_when_backend_advertises_support():
    caps = BackendCapabilities(name="omniverse", material_slot_labeling=True)

    decision = choose_strategy(
        _rule({"target_type": "material_slot", "material_slot": "Glass"}),
        caps,
    )

    assert decision.kind == StrategyKind.DIRECT_MATERIAL_SLOT
    assert decision.executable is True
    assert decision.backend == "omniverse"


def test_instance_target_is_deferred_when_backend_lacks_direct_support():
    caps = BackendCapabilities(name="test", component_labeling=True)

    decision = choose_strategy(
        _rule({"target_type": "instance", "instance_index": "3"}),
        caps,
    )

    assert decision.kind == StrategyKind.REQUIRES_INSTANCE_SPLIT
    assert decision.executable is False


def test_unlabeled_rule_uses_noop_mask_strategy():
    caps = BackendCapabilities(name="test", component_labeling=True)

    decision = choose_strategy(
        _rule({"render_custom_depth": "false", "stencil": ""}),
        caps,
    )

    assert decision.kind == StrategyKind.NOOP
    assert decision.executable is True


def test_invalid_explicit_target_type_is_not_downgraded_to_component():
    caps = BackendCapabilities(name="test", component_labeling=True)
    rule = _rule({"target_type": "material_solt", "material_slot": "Glass"})

    decision = choose_strategy(rule, caps)

    assert rule.invalid_target_type is True
    assert rule.target_type_raw == "material_solt"
    assert decision.kind == StrategyKind.UNSUPPORTED
    assert decision.executable is False
    assert "material_solt" in decision.reason
