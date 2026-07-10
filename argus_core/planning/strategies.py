"""Backend-independent capability and annotation strategy planning."""

from dataclasses import dataclass
from enum import Enum

from argus_core.model import AnnotationRule, RenderPolicy, TargetType


class StrategyKind(str, Enum):
    """Generic execution strategies selected from backend capabilities."""

    DIRECT_COMPONENT = "direct_component"
    DIRECT_MATERIAL_SLOT = "direct_material_slot"
    DIRECT_INSTANCE = "direct_instance"
    DIRECT_PROXY = "direct_proxy"
    REQUIRES_MATERIAL_SPLIT = "requires_material_split"
    REQUIRES_INSTANCE_SPLIT = "requires_instance_split"
    UNSUPPORTED = "unsupported"
    NOOP = "noop"


@dataclass(frozen=True)
class BackendCapabilities:
    """Target granularities that a backend can label directly."""

    name: str
    component_labeling: bool = False
    material_slot_labeling: bool = False
    instance_labeling: bool = False
    proxy_labeling: bool = False


@dataclass(frozen=True)
class StrategyDecision:
    """Planner decision for one normalized annotation rule."""

    backend: str
    kind: StrategyKind
    executable: bool
    reason: str


def choose_strategy(rule: AnnotationRule, capabilities: BackendCapabilities):
    """Choose a strategy solely from target type and advertised capabilities."""
    if rule.invalid_target_type:
        return _decision(
            capabilities,
            StrategyKind.UNSUPPORTED,
            False,
            "Invalid target_type '{}'.".format(rule.target_type_raw),
        )

    if rule.render_policy in {
        RenderPolicy.VISIBLE_UNLABELED,
        RenderPolicy.HIDDEN_UNLABELED,
    }:
        return _decision(
            capabilities,
            StrategyKind.NOOP,
            True,
            "Rule does not enter the mask stream.",
        )

    target_type = rule.target.target_type

    if target_type == TargetType.COMPONENT:
        if capabilities.component_labeling:
            return _decision(
                capabilities,
                StrategyKind.DIRECT_COMPONENT,
                True,
                "Backend can label the component target directly.",
            )
        return _unsupported(capabilities, target_type)

    if target_type == TargetType.MATERIAL_SLOT:
        if capabilities.material_slot_labeling:
            return _decision(
                capabilities,
                StrategyKind.DIRECT_MATERIAL_SLOT,
                True,
                "Backend can label the material slot target directly.",
            )
        return _decision(
            capabilities,
            StrategyKind.REQUIRES_MATERIAL_SPLIT,
            False,
            "Backend cannot label a material slot directly; split or proxy geometry is required.",
        )

    if target_type == TargetType.INSTANCE:
        if capabilities.instance_labeling:
            return _decision(
                capabilities,
                StrategyKind.DIRECT_INSTANCE,
                True,
                "Backend can label the instance target directly.",
            )
        return _decision(
            capabilities,
            StrategyKind.REQUIRES_INSTANCE_SPLIT,
            False,
            "Backend cannot label one instance directly; instance extraction or a proxy is required.",
        )

    if target_type == TargetType.PROXY:
        if capabilities.proxy_labeling:
            return _decision(
                capabilities,
                StrategyKind.DIRECT_PROXY,
                True,
                "Backend can label the explicit proxy target directly.",
            )
        return _unsupported(capabilities, target_type)

    return _unsupported(capabilities, target_type)


def _unsupported(capabilities, target_type):
    return _decision(
        capabilities,
        StrategyKind.UNSUPPORTED,
        False,
        "Backend '{}' cannot label target type '{}' directly.".format(
            capabilities.name,
            target_type.value,
        ),
    )


def _decision(capabilities, kind, executable, reason):
    return StrategyDecision(
        backend=capabilities.name,
        kind=kind,
        executable=bool(executable),
        reason=reason,
    )
