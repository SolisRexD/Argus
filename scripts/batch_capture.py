"""
根据 camera_poses.csv 批量采集多帧图像。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 读取 camera_poses.csv 中的相机位姿。
3. 对每一行 pose 执行一次多路采集。
4. 导出 RGB / MASK / 其他 stream 图像。
5. 将每一帧的 metadata 追加写入 metadata CSV。
6. 支持断点续跑：跳过已经完成且输出文件完整存在的 capture_id。
7. 支持清理 metadata.csv 中文件缺失的不完整记录。

稳健性增强：
- 不仅检查 metadata.csv 中是否已有 capture_id，
  还检查该 capture_id 对应的预期输出文件是否真实存在。
- 如果 metadata 中有记录，但文件不完整，则不会跳过，会重新采集。
- 可选清理 metadata.csv 中的不完整旧记录。
- 本次采集完成后，也会校验输出文件是否真实存在；若缺失则报错，不写 metadata。
- 自动处理 pose CSV 中重复的 capture_id。
- batch 开始前打印统计摘要。
"""

import csv
import json
import os
import sys
import traceback


# ---------------------------------------------------------
# 让当前脚本所在目录可以被 Python import
# ---------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

for path in [PROJECT_ROOT, SCRIPT_DIR]:
    if path not in sys.path:
        sys.path.insert(0, path)


from argus_components import CaptureService, DataPipelineService
from argus_core.capture import (
    check_required_stream_files,
    expected_stream_names,
    extract_stream_file_map,
    validate_capture_outputs,
)
from common import load_json_config, log, resolve_path


def _normalize_capture_id(value, fallback=None):
    """
    规范化 capture_id。

    - 去掉首尾空白。
    - 如果为空，则使用 fallback。
    """
    text = str(value or "").strip()
    return text if text else str(fallback or "").strip()


def _get_expected_stream_names(cfg):
    """
    从配置中推断本次采集期望输出的 stream 名称列表。

    优先级：
    1. cfg["capture"]["streams"] 中显式配置的 name。
    2. 旧版兼容默认值 ["rgb", "mask"]。
    """
    return expected_stream_names(cfg)

    capture_cfg = cfg.get("capture", {})
    streams_cfg = capture_cfg.get("streams", [])

    names = []

    if streams_cfg:
        for row in streams_cfg:
            name = str(row.get("name", "")).strip()
            if name:
                names.append(name)

    if names:
        seen = set()
        result = []

        for name in names:
            if name not in seen:
                result.append(name)
                seen.add(name)

        return result

    return ["rgb", "mask"]


def _extract_stream_file_map_from_row(row):
    """
    从 metadata 的一行中提取 stream -> 文件路径 的映射。

    支持来源：
    1. files_json 字段。
    2. 任意 xxx_file 字段，例如：
       - rgb_file
       - mask_file
       - depth_file
       - normal_file
    """
    return extract_stream_file_map(row)

    file_map = {}

    files_json = row.get("files_json", "")

    if files_json:
        try:
            obj = json.loads(files_json)

            if isinstance(obj, dict):
                for k, v in obj.items():
                    key = str(k).strip()
                    value = str(v).strip() if v is not None else ""

                    if key and value:
                        file_map[key] = value

        except Exception:
            pass

    for key, value in row.items():
        if not key.endswith("_file"):
            continue

        stream_name = key[:-5].strip()
        path = str(value or "").strip()

        if stream_name and path and stream_name not in file_map:
            file_map[stream_name] = path

    return file_map


def _check_required_stream_files(file_map, expected_streams):
    """
    检查预期 stream 对应文件是否都存在。

    返回：
    - ok: bool
    - missing: list[str]
    - bad_paths: dict[str, str]
    """
    return check_required_stream_files(file_map, expected_streams)

    missing = []
    bad_paths = {}

    for stream in expected_streams:
        path = str(file_map.get(stream, "") or "").strip()

        if not path:
            missing.append(stream)
            continue

        if not os.path.exists(path):
            bad_paths[stream] = path

    ok = (len(missing) == 0 and len(bad_paths) == 0)

    return ok, missing, bad_paths


def _load_metadata_rows(metadata_csv):
    """
    读取 metadata.csv。

    返回：
    - rows
    - fieldnames

    如果文件不存在，返回空列表和空表头。
    """
    if not os.path.exists(metadata_csv):
        return [], []

    with open(metadata_csv, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    return rows, fieldnames


def _write_metadata_rows(metadata_csv, rows, fieldnames):
    """
    重写 metadata.csv。

    用于清理不完整记录后保存干净版本。
    """
    directory = os.path.dirname(metadata_csv)
    if directory and not os.path.exists(directory):
        os.makedirs(directory)

    with open(metadata_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _analyze_existing_metadata(metadata_csv, expected_streams, require_all_files=True):
    """
    分析已有 metadata.csv。

    返回：
    - completed_ids: set[str]
    - incomplete_ids: set[str]
    - clean_rows: list[dict]
    - incomplete_rows: list[dict]
    - fieldnames: list[str]
    - issues: list[str]

    completed_ids：
        已经完成且文件完整的 capture_id。

    incomplete_ids：
        metadata 中存在，但输出文件不完整的 capture_id。

    clean_rows：
        完整记录。

    incomplete_rows：
        不完整记录。
    """
    completed_ids = set()
    incomplete_ids = set()
    clean_rows = []
    incomplete_rows = []
    issues = []

    try:
        rows, fieldnames = _load_metadata_rows(metadata_csv)
    except Exception as e:
        issues.append("读取 metadata.csv 失败，将视为无历史记录: {}".format(e))
        return set(), set(), [], [], [], issues

    if not rows:
        return completed_ids, incomplete_ids, clean_rows, incomplete_rows, fieldnames, issues

    if "capture_id" not in fieldnames:
        issues.append("metadata.csv 缺少 capture_id 列，将视为无历史记录")
        return set(), set(), [], rows, fieldnames, issues

    for row_idx, row in enumerate(rows, start=2):
        cid = _normalize_capture_id(row.get("capture_id", ""))

        if not cid:
            incomplete_rows.append(row)
            issues.append("metadata 第 {} 行 capture_id 为空，视为不完整记录".format(row_idx))
            continue

        if not require_all_files:
            completed_ids.add(cid)
            clean_rows.append(row)
            continue

        file_map = _extract_stream_file_map_from_row(row)
        ok, missing, bad_paths = _check_required_stream_files(file_map, expected_streams)

        if ok:
            completed_ids.add(cid)
            clean_rows.append(row)
        else:
            incomplete_ids.add(cid)
            incomplete_rows.append(row)

            detail_parts = []

            if missing:
                detail_parts.append("缺少字段/路径: {}".format(", ".join(missing)))

            if bad_paths:
                detail_parts.append(
                    "文件不存在: {}".format(
                        ", ".join(["{}={}".format(k, v) for k, v in bad_paths.items()])
                    )
                )

            issues.append(
                "metadata 第 {} 行 capture_id={} 不完整，{}".format(
                    row_idx,
                    cid,
                    "；".join(detail_parts),
                )
            )

    return completed_ids, incomplete_ids, clean_rows, incomplete_rows, fieldnames, issues


def _clean_incomplete_metadata(metadata_csv, clean_rows, fieldnames, incomplete_rows):
    """
    从 metadata.csv 中移除不完整记录。

    返回：
    - removed_count
    """
    if not incomplete_rows:
        return 0

    if not fieldnames:
        return 0

    _write_metadata_rows(metadata_csv, clean_rows, fieldnames)

    return len(incomplete_rows)


def _validate_capture_row_outputs(capture_row, expected_streams):
    """
    校验本次 capture_once 返回的 row 中，预期输出文件是否都真实存在。

    如果缺失，抛出 RuntimeError。
    """
    validate_capture_outputs(capture_row, expected_streams)
    return

    file_map = _extract_stream_file_map_from_row(capture_row)
    ok, missing, bad_paths = _check_required_stream_files(file_map, expected_streams)

    if ok:
        return

    parts = []

    if missing:
        parts.append("缺少输出字段/路径: {}".format(", ".join(missing)))

    if bad_paths:
        parts.append(
            "输出文件不存在: {}".format(
                ", ".join(["{}={}".format(k, v) for k, v in bad_paths.items()])
            )
        )

    raise RuntimeError("采集结果不完整：{}".format("；".join(parts)))


def _build_pose_plan(poses, completed_capture_ids):
    """
    根据 poses 和已有完整 capture_id 构建执行计划。

    返回：
    - plan: list[dict]
    - stats: dict

    plan 每项包括：
    - index
    - pose
    - capture_id
    - action: capture / skip_existing / skip_duplicate_pose
    """
    plan = []
    seen_pose_capture_ids = set()

    stats = {
        "total_poses": len(poses),
        "to_capture": 0,
        "skip_existing": 0,
        "skip_duplicate_pose": 0,
    }

    for i, pose in enumerate(poses, start=1):
        capture_id = _normalize_capture_id(
            pose.get("id"),
            fallback="pose_{:06d}".format(i),
        )

        if capture_id in seen_pose_capture_ids:
            action = "skip_duplicate_pose"
            stats["skip_duplicate_pose"] += 1
        elif capture_id in completed_capture_ids:
            action = "skip_existing"
            stats["skip_existing"] += 1
            seen_pose_capture_ids.add(capture_id)
        else:
            action = "capture"
            stats["to_capture"] += 1
            seen_pose_capture_ids.add(capture_id)

        plan.append(
            {
                "index": i,
                "pose": pose,
                "capture_id": capture_id,
                "action": action,
            }
        )

    return plan, stats


def _log_plan_summary(stats, expected_streams, incomplete_count, cleaned_count):
    """
    打印 batch 执行摘要。
    """
    log("========== Batch Plan ==========")
    log("预期 streams: {}".format(", ".join(expected_streams)))
    log("pose 总数: {}".format(stats["total_poses"]))
    log("计划采集: {}".format(stats["to_capture"]))
    log("跳过已完成: {}".format(stats["skip_existing"]))
    log("跳过重复 pose id: {}".format(stats["skip_duplicate_pose"]))
    log("metadata 不完整旧记录: {}".format(incomplete_count))
    log("已清理 metadata 不完整记录: {}".format(cleaned_count))
    log("================================")


def run_batch(config_path=None):
    """
    批量采集入口函数。

    参数：
    - config_path:
        配置文件路径。
        如果为空，则由 load_json_config 使用默认配置路径。

    关键行为：
    1. 读取 poses_csv。
    2. 分析已有 metadata.csv。
    3. 可选清理 metadata.csv 中不完整记录。
    4. 构建执行计划。
    5. 跳过已完成且文件完整的 capture_id。
    6. 对缺失文件的旧记录重新采集。
    7. 对新采集结果检查输出文件是否真实存在。
    """
    cfg, _ = load_json_config(config_path)

    batch_cfg = cfg.get("batch", {})
    output_cfg = cfg["output"]

    poses_csv = resolve_path(batch_cfg.get("poses_csv", "config/camera_poses.csv"))
    metadata_csv = resolve_path(output_cfg["metadata_csv"])

    continue_on_error = bool(batch_cfg.get("continue_on_error", True))

    skip_existing_capture_id = bool(batch_cfg.get("skip_existing_capture_id", True))
    skip_requires_all_files = bool(batch_cfg.get("skip_requires_all_files", True))
    clean_incomplete_metadata = bool(batch_cfg.get("clean_incomplete_metadata", False))

    pipeline = DataPipelineService()
    poses = pipeline.load_poses(poses_csv)

    if not poses:
        raise RuntimeError("没有读取到任何相机位姿: {}".format(poses_csv))

    expected_streams = _get_expected_stream_names(cfg)

    completed_capture_ids = set()
    incomplete_ids = set()
    clean_rows = []
    incomplete_rows = []
    fieldnames = []
    issues = []

    if skip_existing_capture_id:
        (
            completed_capture_ids,
            incomplete_ids,
            clean_rows,
            incomplete_rows,
            fieldnames,
            issues,
        ) = _analyze_existing_metadata(
            metadata_csv=metadata_csv,
            expected_streams=expected_streams,
            require_all_files=skip_requires_all_files,
        )

    cleaned_count = 0

    if clean_incomplete_metadata and incomplete_rows:
        cleaned_count = _clean_incomplete_metadata(
            metadata_csv=metadata_csv,
            clean_rows=clean_rows,
            fieldnames=fieldnames,
            incomplete_rows=incomplete_rows,
        )

        log("已从 metadata.csv 清理不完整记录 {} 条".format(cleaned_count))

    preview_limit = 20

    if issues:
        log("检测到 metadata 历史问题: {} 条".format(len(issues)))

        for msg in issues[:preview_limit]:
            log(msg)

        if len(issues) > preview_limit:
            log("其余 {} 条历史问题已省略...".format(len(issues) - preview_limit))

    if not skip_existing_capture_id:
        completed_capture_ids = set()

    plan, stats = _build_pose_plan(
        poses=poses,
        completed_capture_ids=completed_capture_ids,
    )

    _log_plan_summary(
        stats=stats,
        expected_streams=expected_streams,
        incomplete_count=len(incomplete_ids),
        cleaned_count=cleaned_count,
    )

    capture_service = CaptureService()

    ok = 0
    failed = 0
    skipped_existing = 0
    skipped_duplicate_pose = 0

    for item in plan:
        pose = item["pose"]
        capture_id = item["capture_id"]
        action = item["action"]

        if action == "skip_duplicate_pose":
            skipped_duplicate_pose += 1
            log("跳过重复 pose capture_id: {}".format(capture_id))
            continue

        if action == "skip_existing":
            skipped_existing += 1
            log("跳过已完成 capture_id: {}".format(capture_id))
            continue

        try:
            row = capture_service.capture_once(
                cfg,
                capture_id=capture_id,
                pose=pose,
            )

            _validate_capture_row_outputs(row, expected_streams)

            pipeline.append_capture_metadata(metadata_csv, row)

            completed_capture_ids.add(capture_id)

            ok += 1
            log("采集成功: {}".format(capture_id))

        except Exception as e:
            failed += 1

            log("批量采集失败: capture_id={}, error={}".format(capture_id, e))
            log(traceback.format_exc())

            if not continue_on_error:
                raise

    log("========== Batch Done ==========")
    log("success: {}".format(ok))
    log("failed: {}".format(failed))
    log("skipped_existing: {}".format(skipped_existing))
    log("skipped_duplicate_pose: {}".format(skipped_duplicate_pose))
    log("metadata_cleaned: {}".format(cleaned_count))
    log("================================")


if __name__ == "__main__":
    run_batch()
