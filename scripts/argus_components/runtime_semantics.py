"""Runtime semantic stencil fallback for streamed UE worlds."""

import unreal

from argus_core.semantics import infer_semantic_stencil, load_semantic_alias_rules
from common import get_all_level_actors, log, parse_bool, parse_float, parse_int, resolve_path, warn


class RuntimeSemanticStencilController:
    """Apply heuristic semantic stencils to currently loaded primitive components."""

    def __init__(self, actor_provider=None, log_fn=None, warn_fn=None):
        self._actor_provider = actor_provider or get_all_level_actors
        self._log = log_fn or log
        self._warn = warn_fn or warn

    def apply(self, cfg, pose=None):
        runtime_cfg = cfg.get("runtime", {})
        options = runtime_cfg.get("auto_semantic_stencil", {})
        enabled = parse_bool(options.get("enabled"), default=False)

        stats = {
            "enabled": bool(enabled),
            "component_order": "source",
            "scanned_components": 0,
            "changed": 0,
            "preserved": 0,
            "preserved_enabled": 0,
            "ignored": 0,
            "failed": 0,
            "stopped_at_limit": False,
            "changed_by_class": {},
            "preserved_by_stencil": {},
            "unmatched_samples": [],
            "alias_rules_loaded": 0,
        }

        if not enabled:
            return stats

        preserve_existing = parse_bool(options.get("preserve_existing"), default=True)
        unknown_for_unmatched = parse_bool(options.get("unknown_for_unmatched"), default=True)
        max_components = parse_int(options.get("max_components"), default=20000)
        unmatched_sample_limit = max(0, parse_int(options.get("unmatched_sample_limit"), default=20))
        component_order = str(options.get("component_order", "source") or "source").strip().lower()
        capture_point = self._resolve_capture_point(pose, options, runtime_cfg)
        alias_rules = self._load_alias_rules(options, stats)

        try:
            actors = list(self._actor_provider())
        except Exception as exc:
            self._warn("Runtime semantic stencil actor scan failed: {}".format(exc))
            stats["failed"] += 1
            return stats

        component_items = self._iter_component_items(
            actors,
            component_order=component_order,
            capture_point=capture_point,
        )
        if component_order == "capture_distance" and capture_point:
            stats["component_order"] = component_order

        for actor, component in component_items:
            if max_components and stats["scanned_components"] >= max_components:
                stats["stopped_at_limit"] = True
                self._log("Runtime semantic stencil stopped at component limit {}".format(max_components))
                self._log_summary(stats)
                return stats

            stats["scanned_components"] += 1

            existing_stencil = self._get_int_property(component, "custom_depth_stencil_value", 0)
            fields = self._collect_fields(actor, component)
            component_fields = self._collect_component_fields(component)

            if preserve_existing and existing_stencil > 0:
                stats["preserved"] += 1
                self._increment_count(stats["preserved_by_stencil"], str(existing_stencil))

                enabled = self._ensure_component_custom_depth(component)
                if enabled is True:
                    stats["preserved_enabled"] += 1
                elif enabled is None:
                    stats["failed"] += 1

                continue

            decision = self._infer_component_semantic_stencil(
                fields,
                component_fields,
                unknown_for_unmatched=False,
                alias_rules=alias_rules,
            )

            if decision is None:
                if unknown_for_unmatched:
                    decision = self._infer_component_semantic_stencil(
                        fields,
                        component_fields,
                        unknown_for_unmatched=True,
                        alias_rules=alias_rules,
                    )

                    if decision is None:
                        stats["ignored"] += 1
                        continue

                    self._append_unmatched_sample(
                        stats["unmatched_samples"],
                        fields,
                        unmatched_sample_limit,
                    )
                else:
                    self._append_unmatched_sample(
                        stats["unmatched_samples"],
                        fields,
                        unmatched_sample_limit,
                    )
                    stats["ignored"] += 1
                    continue

            if self._set_component_stencil(component, decision.stencil):
                stats["changed"] += 1
                by_class = stats["changed_by_class"]
                self._increment_count(by_class, decision.semantic_class)
            else:
                stats["failed"] += 1

        self._log_summary(stats)
        return stats

    def _infer_component_semantic_stencil(
        self,
        fields,
        component_fields,
        unknown_for_unmatched=False,
        alias_rules=(),
    ):
        if alias_rules:
            decision = infer_semantic_stencil(
                component_fields,
                unknown_for_unmatched=False,
                rules=alias_rules,
            )

            if decision:
                return decision

        return infer_semantic_stencil(
            fields,
            unknown_for_unmatched=unknown_for_unmatched,
            rules=alias_rules,
        )

    def _load_alias_rules(self, options, stats):
        alias_csv = str(options.get("aliases_csv") or "").strip()

        if not alias_csv:
            return ()

        alias_path = resolve_path(alias_csv)
        stats["alias_rules_path"] = alias_path

        try:
            rules = load_semantic_alias_rules(alias_path)
        except Exception as exc:
            self._warn("Runtime semantic alias CSV load failed: {}".format(exc))
            stats["alias_rules_error"] = str(exc)
            return ()

        stats["alias_rules_loaded"] = len(rules)
        return rules

    def _log_summary(self, stats):
        self._log(
            "Runtime semantic stencil: order={}, scanned={}, changed={}, preserved={}, preserved_enabled={}, ignored={}, failed={}, alias_rules_loaded={}, by_class={}, preserved_by_stencil={}, unmatched_samples={}".format(
                stats.get("component_order", "source"),
                stats.get("scanned_components", 0),
                stats.get("changed", 0),
                stats.get("preserved", 0),
                stats.get("preserved_enabled", 0),
                stats.get("ignored", 0),
                stats.get("failed", 0),
                stats.get("alias_rules_loaded", 0),
                stats.get("changed_by_class", {}),
                stats.get("preserved_by_stencil", {}),
                stats.get("unmatched_samples", []),
            )
        )

    def _iter_component_items(self, actors, component_order="source", capture_point=None):
        if component_order != "capture_distance" or not capture_point:
            for actor in actors:
                for component in self._get_primitive_components(actor):
                    yield actor, component

            return

        items = []
        sequence = 0

        for actor in actors:
            for component in self._get_primitive_components(actor):
                location = self._component_location(component) or self._actor_location(actor)
                distance = self._distance_squared(location, capture_point)
                items.append(
                    (
                        distance is None,
                        distance if distance is not None else 0.0,
                        sequence,
                        actor,
                        component,
                    )
                )
                sequence += 1

        for _, _, _, actor, component in sorted(items, key=lambda item: item[:3]):
            yield actor, component

    def _get_primitive_components(self, actor):
        if not actor:
            return []

        try:
            return list(actor.get_components_by_class(unreal.PrimitiveComponent))
        except Exception:
            return []

    def _collect_fields(self, actor, component):
        return [
            self._actor_label(actor),
            self._object_name(actor),
            self._class_name(actor),
        ] + self._collect_component_fields(component)

    def _collect_component_fields(self, component):
        fields = [
            self._object_name(component),
            self._class_name(component),
        ]

        for prop_name in ("static_mesh", "skeletal_mesh"):
            asset = self._get_editor_property(component, prop_name, None)
            if asset:
                fields.append(self._object_name(asset))
                fields.append(self._object_path(asset))

        for material in self._get_materials(component):
            fields.append(self._object_name(material))
            fields.append(self._object_path(material))

        return fields

    def _actor_label(self, actor):
        try:
            return actor.get_actor_label()
        except Exception:
            return ""

    def _object_name(self, obj):
        try:
            return obj.get_name()
        except Exception:
            return ""

    def _object_path(self, obj):
        try:
            return obj.get_path_name()
        except Exception:
            return ""

    def _class_name(self, obj):
        try:
            return obj.get_class().get_name()
        except Exception:
            return ""

    def _get_materials(self, component):
        try:
            return [m for m in list(component.get_materials()) if m]
        except Exception:
            return []

    def _get_editor_property(self, obj, prop_name, default=None):
        try:
            return obj.get_editor_property(prop_name)
        except Exception:
            return default

    def _get_int_property(self, obj, prop_name, default=0):
        try:
            return int(obj.get_editor_property(prop_name))
        except Exception:
            return int(default)

    def _get_bool_property(self, obj, prop_name, default=False):
        try:
            return bool(obj.get_editor_property(prop_name))
        except Exception:
            return bool(default)

    def _resolve_capture_point(self, pose, options, runtime_cfg):
        for value in (
            pose,
            options.get("capture_point"),
            runtime_cfg.get("capture_point"),
        ):
            point = self._xyz_tuple(value)
            if point:
                return point

        return None

    def _xyz_tuple(self, value):
        if not value:
            return None

        if isinstance(value, dict):
            x = parse_float(value.get("x"), default=None)
            y = parse_float(value.get("y"), default=None)
            z = parse_float(value.get("z"), default=None)
        else:
            x = parse_float(getattr(value, "x", None), default=None)
            y = parse_float(getattr(value, "y", None), default=None)
            z = parse_float(getattr(value, "z", None), default=None)

        if x is None or y is None or z is None:
            return None

        return (float(x), float(y), float(z))

    def _component_location(self, component):
        return self._object_location(
            component,
            ("get_component_location", "get_world_location"),
        )

    def _actor_location(self, actor):
        return self._object_location(actor, ("get_actor_location",))

    def _object_location(self, obj, method_names):
        if not obj:
            return None

        for method_name in method_names:
            try:
                method = getattr(obj, method_name)
                return self._xyz_tuple(method())
            except Exception:
                pass

        return None

    def _distance_squared(self, location, capture_point):
        if not location or not capture_point:
            return None

        dx = float(location[0]) - float(capture_point[0])
        dy = float(location[1]) - float(capture_point[1])
        dz = float(location[2]) - float(capture_point[2])
        return dx * dx + dy * dy + dz * dz

    def _set_component_stencil(self, component, stencil):
        try:
            component.set_editor_property("render_custom_depth", True)
            component.set_editor_property("custom_depth_stencil_value", int(stencil))
            return True
        except Exception as exc:
            self._warn("Unable to set runtime semantic stencil: {}".format(exc))
            return False

    def _ensure_component_custom_depth(self, component):
        was_enabled = self._get_bool_property(component, "render_custom_depth", False)

        try:
            component.set_editor_property("render_custom_depth", True)
        except Exception as exc:
            self._warn("Unable to enable preserved semantic stencil: {}".format(exc))
            return None

        return not was_enabled

    def _increment_count(self, mapping, key):
        mapping[key] = mapping.get(key, 0) + 1

    def _format_unmatched_sample(self, fields):
        compact_fields = []
        for field in fields:
            text = str(field or "").strip()
            if text:
                compact_fields.append(text)

            if len(compact_fields) >= 8:
                break

        return " | ".join(compact_fields)

    def _append_unmatched_sample(self, samples, fields, limit):
        if limit <= 0 or len(samples) >= limit:
            return

        sample = self._format_unmatched_sample(fields)

        if sample and sample not in samples:
            samples.append(sample)
