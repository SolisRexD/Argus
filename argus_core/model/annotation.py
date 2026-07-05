"""Annotation target and rule model independent from engine APIs."""

from dataclasses import dataclass, field
from enum import Enum


class TargetType(str, Enum):
    """Supported annotation target granularities."""

    COMPONENT = "component"
    MATERIAL_SLOT = "material_slot"
    INSTANCE = "instance"
    PROXY = "proxy"


class RenderPolicy(str, Enum):
    """Normalized RGB and mask visibility policy."""

    VISIBLE_LABELED = "visible_labeled"
    VISIBLE_UNLABELED = "visible_unlabeled"
    HIDDEN_LABELED = "hidden_labeled"
    HIDDEN_UNLABELED = "hidden_unlabeled"


@dataclass(frozen=True)
class AnnotationTarget:
    """Engine-neutral target address for a semantic annotation rule."""

    target_type: TargetType
    actor_name: str
    component_name: str
    mesh_name: str = ""
    mesh_path: str = ""
    material_name: str = ""
    material_path: str = ""
    material_slot: str = ""
    instance_index: int | None = None
    proxy_id: str = ""

    @classmethod
    def from_legacy_row(cls, row):
        instance_index = _optional_int(row.get("instance_index"))
        proxy_id = _text(row.get("proxy_id"))
        material_slot = _text(row.get("material_slot"))
        material_name = _text(row.get("material_name"))
        material_path = _text(row.get("material_path"))

        if proxy_id:
            target_type = TargetType.PROXY
        elif instance_index is not None:
            target_type = TargetType.INSTANCE
        elif material_slot or material_name or material_path:
            target_type = TargetType.MATERIAL_SLOT
        else:
            target_type = TargetType.COMPONENT

        return cls(
            target_type=target_type,
            actor_name=_text(row.get("actor_name")),
            component_name=_text(row.get("component_name")),
            mesh_name=_text(row.get("mesh_name")),
            mesh_path=_text(row.get("mesh_path")),
            material_name=material_name,
            material_path=material_path,
            material_slot=material_slot,
            instance_index=instance_index,
            proxy_id=proxy_id,
        )


@dataclass(frozen=True)
class AnnotationRule:
    """A normalized semantic annotation rule."""

    target: AnnotationTarget
    semantic_class: str
    render_policy: RenderPolicy
    effective_stencil: int | None
    stencil_override: int | None = None
    invalid_render_switches: bool = False
    extra_fields: dict = field(default_factory=dict)

    @classmethod
    def from_legacy_row(cls, row, unknown_stencil=250, ignore_stencil=254):
        target = AnnotationTarget.from_legacy_row(row)
        render_main, main_invalid = _parse_bool(row.get("render_main_pass"))
        render_depth, depth_invalid = _parse_bool(row.get("render_custom_depth"))
        semantic_class = _text(row.get("semantic_class"))
        stencil_override = _optional_int(row.get("stencil"))

        effective_stencil = stencil_override
        if render_depth and effective_stencil is None:
            effective_stencil = int(unknown_stencil)
        if render_depth and semantic_class.lower() == "ignore":
            effective_stencil = int(ignore_stencil)
        if not render_depth:
            effective_stencil = None

        return cls(
            target=target,
            semantic_class=semantic_class,
            render_policy=_render_policy(render_main, render_depth),
            effective_stencil=effective_stencil,
            stencil_override=stencil_override,
            invalid_render_switches=bool(main_invalid or depth_invalid),
            extra_fields=_extra_fields(row),
        )


def _render_policy(render_main, render_depth):
    if render_main and render_depth:
        return RenderPolicy.VISIBLE_LABELED
    if render_main and not render_depth:
        return RenderPolicy.VISIBLE_UNLABELED
    if not render_main and render_depth:
        return RenderPolicy.HIDDEN_LABELED
    return RenderPolicy.HIDDEN_UNLABELED


def _parse_bool(value):
    text = _text(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True, False
    if text in {"0", "false", "no", "n", "off"}:
        return False, False
    return False, True


def _optional_int(value):
    text = _text(value)
    if not text:
        return None
    try:
        return int(text)
    except Exception:
        return None


def _text(value):
    return str(value or "").strip()


def _extra_fields(row):
    known = {
        "actor_name",
        "component_name",
        "mesh_name",
        "mesh_path",
        "material_name",
        "material_path",
        "material_slot",
        "instance_index",
        "proxy_id",
        "semantic_class",
        "render_main_pass",
        "render_custom_depth",
        "stencil",
    }
    return {k: v for k, v in dict(row).items() if k not in known}
