"""Shared capture output validation helpers."""

import json
import os


def expected_stream_names(cfg):
    """Return unique expected capture stream names from config."""
    capture_cfg = cfg.get("capture", {})
    streams_cfg = capture_cfg.get("streams", [])
    names = []

    for row in streams_cfg:
        name = str(row.get("name", "")).strip()
        if name and name not in names:
            names.append(name)

    if names:
        return names

    return ["rgb", "mask"]


def extract_stream_file_map(row):
    """Extract a stream-name to file-path map from metadata or capture rows."""
    file_map = {}
    files_json = row.get("files_json", "")

    if files_json:
        try:
            obj = json.loads(files_json)
        except Exception:
            obj = None

        if isinstance(obj, dict):
            for key, value in obj.items():
                stream_name = str(key).strip()
                path = str(value).strip() if value is not None else ""
                if stream_name and path:
                    file_map[stream_name] = path

    for key, value in row.items():
        if not key.endswith("_file"):
            continue

        stream_name = key[:-5].strip()
        path = str(value or "").strip()

        if stream_name and path and stream_name not in file_map:
            file_map[stream_name] = path

    return file_map


def check_required_stream_files(file_map, expected_streams):
    """Check that all required stream files exist on disk."""
    missing = []
    bad_paths = {}

    for stream in expected_streams:
        path = str(file_map.get(stream, "") or "").strip()

        if not path:
            missing.append(stream)
            continue

        if not os.path.exists(path):
            bad_paths[stream] = path

    return len(missing) == 0 and len(bad_paths) == 0, missing, bad_paths


def validate_capture_outputs(row, expected_streams):
    """Return file map or raise RuntimeError when required outputs are incomplete."""
    file_map = extract_stream_file_map(row)
    ok, missing, bad_paths = check_required_stream_files(file_map, expected_streams)

    if ok:
        return file_map

    parts = []

    if missing:
        parts.append("missing output field/path: {}".format(", ".join(missing)))

    if bad_paths:
        parts.append(
            "output file does not exist: {}".format(
                ", ".join(["{}={}".format(k, v) for k, v in bad_paths.items()])
            )
        )

    raise RuntimeError("capture output incomplete: {}".format("; ".join(parts)))
