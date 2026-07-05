"""
在正式语义回写前，校验 semantic_map.csv 是否能匹配当前 UE 场景。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 pipeline_config.json。
2. 读取 semantic_map.csv。
3. 扫描当前 UE 场景组件。
4. 检查每一条语义规则是否能解析到唯一组件。
5. 检查 render_main_pass / render_custom_depth 开关是否合法。
6. 检查 stencil 是否缺失。
7. 检查重复规则。
8. 检查组件是否支持 CustomDepth / CustomStencil。
9. 检查半透明材质是否可能没有开启 Allow Custom Depth Writes。
10. 输出 semantic_map_validation.csv。

注意：
- 本脚本只做校验，不修改 UE 场景。
- 真正写回 stencil 和渲染开关的是 writeback 脚本。
"""

import csv
import os
import sys


# ---------------------------------------------------------
# 让当前脚本所在目录可以被 Python import
# ---------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()

if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


from argus_components import AnnotationController, SceneObjectCatalog, SemanticRuleBuilder
from common import load_json_config, log, resolve_path, semantic_map_to_stencil


def _compose_match_key(rule):
    """
    为一条规则构建稳定匹配键，用于检测重复规则。

    匹配键包含：
    - actor_name
    - component_name
    - mesh_name
    - mesh_path
    - material_name
    - material_path
    - material_slot
    - instance_index

    这些字段共同决定一条 semantic_map 规则指向哪个组件或组件子目标。
    """
    return (
        str(rule.get("actor_name", "")).strip(),
        str(rule.get("component_name", "")).strip(),
        str(rule.get("mesh_name", "")).strip(),
        str(rule.get("mesh_path", "")).strip(),
        str(rule.get("material_name", "")).strip(),
        str(rule.get("material_path", "")).strip(),
        str(rule.get("material_slot", "")).strip(),
        "" if rule.get("instance_index") is None else str(rule.get("instance_index")).strip(),
    )


def _ensure_parent_dir(path):
    """
    确保输出文件所在目录存在。

    如果 path 没有目录部分，例如：
        semantic_map_validation.csv

    则不创建目录。
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)


def _write_csv(path, rows):
    """
    写出校验结果 CSV。

    使用稳定表头顺序：
    - 常用字段排前面。
    - 额外字段按首次出现顺序追加。
    """
    _ensure_parent_dir(path)

    preferred = [
        "row_index",
        "status",
        "severity",
        "actor_name",
        "component_name",
        "mesh_name",
        "mesh_path",
        "material_name",
        "material_path",
        "material_slot",
        "instance_index",
        "semantic_class",
        "render_main_pass",
        "render_custom_depth",
        "stencil",
        "message",
        "translucent_risk",
        "ignore_stencil",
    ]

    seen = set(preferred)
    fieldnames = list(preferred)

    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _append_issue(current_status, current_severity, current_message, new_status, new_severity, new_message):
    """
    合并同一行规则上的多个校验问题。

    规则：
    - error 优先级高于 warning。
    - warning 优先级高于 info。
    - message 用分号拼接，避免后面的检查覆盖前面的检查。
    """
    severity_rank = {
        "info": 0,
        "warning": 1,
        "error": 2,
    }

    old_rank = severity_rank.get(current_severity, 0)
    new_rank = severity_rank.get(new_severity, 0)

    if new_rank > old_rank:
        final_status = new_status
        final_severity = new_severity
    else:
        final_status = current_status
        final_severity = current_severity

    if current_message and current_message != "resolved":
        final_message = "{}；{}".format(current_message, new_message)
    else:
        final_message = new_message

    return final_status, final_severity, final_message


def validate_semantic_map(config_path=None):
    """
    校验 semantic_map.csv，并输出 semantic_map_validation.csv。

    参数：
    - config_path:
        配置文件路径。如果为空，则使用默认配置。

    返回：
    - total
    - errors
    - warnings
    - validation_csv
    """
    cfg, cfg_path = load_json_config(config_path)

    sem_cfg = cfg["semantics"]
    output_cfg = cfg["output"]

    semantic_map_csv = resolve_path(sem_cfg["semantic_map_csv"])
    class_table_csv = resolve_path(sem_cfg["class_table_csv"])
    validation_csv = resolve_path(
        output_cfg.get("semantic_validation_csv", "output/semantic_map_validation.csv")
    )

    rules = semantic_map_to_stencil(semantic_map_csv)

    scene_catalog = SceneObjectCatalog()
    component_index = scene_catalog.build_component_index()

    annotator = AnnotationController()
    ignore_stencil = annotator.detect_ignore_stencil(class_table_csv)

    rule_builder = SemanticRuleBuilder(sem_cfg, ignore_stencil)

    rows = []

    duplicate_counter = {}

    for r in rules:
        k = _compose_match_key(r)
        duplicate_counter[k] = duplicate_counter.get(k, 0) + 1

    for i, rule in enumerate(rules, start=1):
        row_ctx = rule_builder.build_context(rule)

        render_main_pass = row_ctx.render_main_pass
        render_custom_depth = row_ctx.render_custom_depth
        stencil = row_ctx.stencil

        descriptor, resolve_status = scene_catalog.resolve_component_descriptor(
            component_index,
            rule,
        )

        status = "ok"
        severity = "info"
        message = "resolved"
        translucent_risk = ""

        # 1. 组件解析检查
        if resolve_status != "ok":
            status = resolve_status
            severity = "error"
            message = "组件解析失败: {}".format(resolve_status)

        # 2. render_main_pass / render_custom_depth 开关检查
        if row_ctx.invalid_render_switches:
            status, severity, message = _append_issue(
                status,
                severity,
                message,
                "invalid_render_switches",
                "warning",
                "render_main_pass 或 render_custom_depth 为空/非法，writeback 将跳过该规则",
            )

        # 3. stencil 检查
        # 理论上 SemanticRuleBuilder 会在 render_custom_depth=true 且 stencil 缺失时
        # 自动回退 unknown_stencil，所以这里更多是保险。
        if render_custom_depth and stencil is None:
            status, severity, message = _append_issue(
                status,
                severity,
                message,
                "missing_stencil",
                "error",
                "render_custom_depth=true 但 stencil 缺失",
            )

        # 4. 重复规则检查
        duplicate_count = duplicate_counter.get(_compose_match_key(rule), 0)
        if duplicate_count > 1:
            status, severity, message = _append_issue(
                status,
                severity,
                message,
                "duplicate_rule",
                "warning",
                "重复匹配规则出现 {} 次".format(duplicate_count),
            )

        # 5. 组件是否支持 CustomDepth / Stencil
        component = descriptor.get("component_ref") if descriptor else None

        if component and not annotator.supports_stencil(component):
            status, severity, message = _append_issue(
                status,
                severity,
                message,
                "component_unsupported",
                "error",
                "组件不支持 CustomDepth / CustomStencil",
            )

        # 6. 半透明材质风险检查
        # 如果该组件要进入 mask，则检查它的材质是否可能不写入 CustomDepth。
        if component and render_custom_depth:
            translucent_risk = annotator.inspect_translucent_material_risk(component)

            if translucent_risk:
                status, severity, message = _append_issue(
                    status,
                    severity,
                    message,
                    "translucent_custom_depth_risk",
                    "warning",
                    translucent_risk,
                )

        rows.append(
            {
                "row_index": i,
                "status": status,
                "severity": severity,
                "actor_name": str(rule.get("actor_name", "")).strip(),
                "component_name": str(rule.get("component_name", "")).strip(),
                "mesh_name": str(rule.get("mesh_name", "")).strip(),
                "mesh_path": str(rule.get("mesh_path", "")).strip(),
                "material_name": str(rule.get("material_name", "")).strip(),
                "material_path": str(rule.get("material_path", "")).strip(),
                "material_slot": str(rule.get("material_slot", "")).strip(),
                "instance_index": "" if rule.get("instance_index") is None else rule.get("instance_index"),
                "semantic_class": str(rule.get("semantic_class", "")).strip(),
                "render_main_pass": render_main_pass,
                "render_custom_depth": render_custom_depth,
                "stencil": "" if stencil is None else stencil,
                "message": message,
                "translucent_risk": translucent_risk,
                "ignore_stencil": ignore_stencil,
            }
        )

    _write_csv(validation_csv, rows)

    total = len(rows)
    errors = len([r for r in rows if r["severity"] == "error"])
    warnings = len([r for r in rows if r["severity"] == "warning"])

    log("配置文件: {}".format(cfg_path))
    log("semantic_map 校验完成: total={}, errors={}, warnings={}".format(total, errors, warnings))
    log("校验结果 CSV: {}".format(validation_csv))

    return {
        "total": total,
        "errors": errors,
        "warnings": warnings,
        "validation_csv": validation_csv,
    }


if __name__ == "__main__":
    validate_semantic_map()