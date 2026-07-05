"""
采集一帧同步图像，并追加写入 metadata。

本脚本是 UE Python 直接运行的入口脚本之一。

它负责：
1. 读取 Argus 配置文件。
2. 调用 CaptureService.capture_once() 执行一次采集。
3. 导出 RGB / MASK / 其他已配置 stream。
4. 校验输出文件是否真实存在。
5. 将本帧 metadata 追加写入 metadata.csv。
6. 打印本次采集结果。

注意：
- 真正的采集逻辑在 CaptureService 中。
- 真正的数据写入逻辑在 DataPipelineService 中。
- 本脚本只负责单帧采集流程调度。
"""

import os
import sys


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
    从 capture row 中提取 stream -> 文件路径。

    支持：
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


def _validate_capture_row_outputs(row, expected_streams):
    """
    校验本次采集的输出文件是否真实存在。

    如果预期 stream 缺少路径或文件不存在，则抛出 RuntimeError。
    """
    return validate_capture_outputs(row, expected_streams)

    file_map = _extract_stream_file_map_from_row(row)

    missing = []
    bad_paths = {}

    for stream in expected_streams:
        path = str(file_map.get(stream, "") or "").strip()

        if not path:
            missing.append(stream)
            continue

        if not os.path.exists(path):
            bad_paths[stream] = path

    if not missing and not bad_paths:
        return file_map

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


def capture_once(config_path=None, capture_id=None, pose=None):
    """
    单帧采集入口函数。

    参数：
    - config_path:
        配置文件路径。如果为空，则使用默认配置。
    - capture_id:
        当前采集 ID。如果为空，由 CaptureService 自动生成。
    - pose:
        可选相机位姿。如果为空，则使用当前 SceneCapture 位置。

    返回：
    - result:
        包含 capture_id 和各 stream 文件路径的字典。
    """
    cfg, _ = load_json_config(config_path)
    output_cfg = cfg["output"]

    expected_streams = expected_stream_names(cfg)

    capture_service = CaptureService()
    row = capture_service.capture_once(
        cfg,
        capture_id=capture_id,
        pose=pose,
    )

    file_map = validate_capture_outputs(
        row,
        expected_streams,
    )

    metadata_csv = resolve_path(output_cfg["metadata_csv"])

    pipeline = DataPipelineService()
    pipeline.append_capture_metadata(metadata_csv, row)

    log("采集完成: {}".format(row["capture_id"]))

    for stream_name in expected_streams:
        log("{}: {}".format(stream_name.upper(), file_map.get(stream_name, "")))

    result = {
        "capture_id": row["capture_id"],
        "files": file_map,
    }

    # 兼容旧版调用者。
    if "rgb" in file_map:
        result["rgb_file"] = file_map["rgb"]

    if "mask" in file_map:
        result["mask_file"] = file_map["mask"]

    return result


if __name__ == "__main__":
    capture_once()
