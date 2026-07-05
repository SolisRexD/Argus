"""
CSV / JSON 数据读写服务。

本模块负责 Argus 管线中的数据文件输入输出，包括：

1. 场景清单：
   - scene_inventory.json
   - scene_inventory.csv

2. 采集元数据：
   - capture_metadata.csv

3. 语义回写日志：
   - writeback_log.csv

4. 相机位姿：
   - camera_poses.csv

注意：
本模块只负责文件读写，不直接操作 Unreal Engine 场景。
"""

import csv
import json
import os

from common import ensure_dir, now_stamp, read_pose_rows


class DataPipelineService:
    """
    数据管线服务。

    负责以稳定的字段顺序写入和追加 Argus 管线产生的数据文件。
    """

    def _ensure_parent_dir(self, path):
        """
        确保目标文件所在目录存在。

        如果 path 没有目录部分，例如：
            metadata.csv

        则 os.path.dirname(path) 会返回空字符串。
        这种情况下不需要创建目录，直接跳过。
        """
        directory = os.path.dirname(path)
        if directory:
            ensure_dir(directory)

    def _fieldnames_from_rows(self, rows, preferred):
        """
        根据推荐字段顺序和实际数据行，合并生成 CSV 表头。

        规则：
        1. preferred 中的字段优先排在前面。
        2. rows 中出现但 preferred 中没有的字段，按首次出现顺序追加。
        3. 保持字段顺序稳定，避免多次运行后 CSV 列顺序混乱。
        """
        seen = set(preferred)
        names = list(preferred)

        for row in rows:
            for key in row.keys():
                if key not in seen:
                    names.append(key)
                    seen.add(key)

        return names

    def write_scene_inventory(self, rows, json_path, csv_path):
        """
        写出场景清单。

        输出两个文件：
        1. JSON：
           - 保留完整结构。
           - 方便程序再次读取。

        2. CSV：
           - 方便人工检查、筛选和交给 LLM 清洗。
           - 使用 utf-8-sig，方便 Excel 正确识别中文。
        """
        self._ensure_parent_dir(json_path)
        self._ensure_parent_dir(csv_path)

        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({"rows": rows}, f, ensure_ascii=False, indent=2)

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = self._fieldnames_from_rows(
                rows,
                [
                    "actor_name",
                    "component_name",
                    "actor_class",
                    "component_class",
                    "actor_path",
                    "component_path",
                    "mesh_name",
                    "mesh_path",
                    "instance_count",
                    "material_names",
                    "material_details",
                ],
            )

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )
            writer.writeheader()
            writer.writerows(rows)

    def append_capture_metadata(self, csv_path, capture_row):
        """
        追加一行采集元数据。

        这里采用“读旧 CSV → 合并表头 → 重写 CSV”的方式，而不是简单 append。

        原因：
        capture_row 可能随着管线扩展出现新字段，例如：
        - depth_file
        - normal_file
        - files_json
        - primary_stream
        - fx_px
        - fy_px
        - cx_px
        - cy_px

        如果直接 append，新字段可能没有表头。
        因此这里会自动合并旧字段和新字段。
        """
        self._ensure_parent_dir(csv_path)

        preferred = [
            "capture_id",
            "timestamp",
            "rgb_file",
            "mask_file",
            "x",
            "y",
            "z",
            "pitch",
            "yaw",
            "roll",
            "fov",
        ]

        row = dict(capture_row)
        row["timestamp"] = now_stamp()

        if os.path.exists(csv_path):
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                existing_rows = list(reader)
                existing_headers = list(reader.fieldnames or [])
        else:
            existing_rows = []
            existing_headers = []

        fieldnames = self._fieldnames_from_rows(
            existing_rows + [row],
            preferred,
        )

        # 保留旧 CSV 中存在、但当前数据行没有出现的历史字段。
        for h in existing_headers:
            if h not in fieldnames:
                fieldnames.append(h)

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerow(row)

    def write_stencil_writeback_log(self, csv_path, logs):
        """
        写出语义 stencil 回写日志。

        这个文件用于检查每一条 semantic_map 规则是否成功应用到 UE 组件。

        常见字段：
        - actor_name
        - component_name
        - semantic_class
        - render_main_pass
        - render_custom_depth
        - stencil
        - status
        - detail
        """
        self._ensure_parent_dir(csv_path)

        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            fieldnames = self._fieldnames_from_rows(
                logs,
                [
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
                    "status",
                    "detail",
                ],
            )

            writer = csv.DictWriter(
                f,
                fieldnames=fieldnames,
            )
            writer.writeheader()
            writer.writerows(logs)

    def load_poses(self, poses_csv):
        """
        读取相机位姿 CSV。

        实际解析逻辑交给 common.read_pose_rows，
        这样 pose 格式的容错和转换可以集中维护。
        """
        return read_pose_rows(poses_csv)