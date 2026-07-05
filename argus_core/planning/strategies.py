"""Backend capability and annotation strategy planning."""

from dataclasses import dataclass
from enum import Enum

from argus_core.model import AnnotationRule, RenderPolicy, TargetType


class StrategyKind(str, Enum):
    """Known backend execution strategies."""

    UE_COMPONENT_STENCIL = "ue_component_stencil"
    UE_PROXY_STENCIL = "ue_proxy_stencil"
    UE_REQUIRES_MATERIAL_SPLIT = "ue_requires_material_split"
    UE_REQUIRES_INSTANCE_SPLIT = "ue_requires_instance_split"
    UNSUPPORTED = "unsupported"
    NOOP = "noop"


@dataclass(frozen=True)
class BackendCapabilities:
    """Capabilities advertised by an engine backend."""

    name: str
    component_custom_stencil: bool = False
    material_slot_custom_stencil: bool = False
    instance_custom_stencil: bool = False
    proxy_custom_stencil: bool = False

    @classmethod
    def ue_default(cls):
        return cls(
            name="ue",
            component_custom_stencil=True,
            material_slot_custom_stencil=False,
            instance_custom_stencil=False,
            proxy_custom_stencil=True,
        )


@dataclass(frozen=True)
class StrategyDecision:
    """Planner decision for one normalized annotation rule."""

    kind: StrategyKind
    executable: bool
    reason: str


def choose_strategy(rule: AnnotationRule, capabilities: BackendCapabilities):
    """Choose a backend strategy for one annotation rule."""
    if rule.render_policy in {
        RenderPolicy.VISIBLE_UNLABELED,
        RenderPolicy.HIDDEN_UNLABELED,
    }:
        return StrategyDecision(
            kind=StrategyKind.NOOP,
            executable=True,
            reason="Rule does not enter the mask stream.",
        )

    if capabilities.name == "ue":
        return _choose_ue_strategy(rule, capabilities)

    return StrategyDecision(
        kind=StrategyKind.UNSUPPORTED,
        executable=False,
        reason="Backend '{}' is not supported by this planner.".format(capabilities.name),
    )


def _choose_ue_strategy(rule, capabilities):
    target_type = rule.target.target_type

    if target_type == TargetType.COMPONENT and capabilities.component_custom_stencil:
        return StrategyDecision(
            kind=StrategyKind.UE_COMPONENT_STENCIL,
            executable=True,
            reason="UE can apply CustomStencil directly to primitive components.",
        )

    if target_type == TargetType.PROXY and capabilities.proxy_custom_stencil:
        return StrategyDecision(
            kind=StrategyKind.UE_PROXY_STENCIL,
            executable=True,
            reason="UE can apply CustomStencil to an explicit proxy component.",
        )

    if target_type == TargetType.MATERIAL_SLOT:
        return StrategyDecision(
            kind=StrategyKind.UE_REQUIRES_MATERIAL_SPLIT,
            executable=False,
            reason="UE CustomStencil is component-level; material slot targets require material slot split or proxy geometry.",
        )

    if target_type == TargetType.INSTANCE:
        return StrategyDecision(
            kind=StrategyKind.UE_REQUIRES_INSTANCE_SPLIT,
            executable=False,
            reason="UE CustomStencil is component-level; instanced targets require instance split or proxy instances.",
        )

    return StrategyDecision(
        kind=StrategyKind.UNSUPPORTED,
        executable=False,
        reason="No UE strategy is available for target type '{}'.".format(target_type.value),
    )
