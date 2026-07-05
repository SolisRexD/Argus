import json

from argus_core.capture import (
    check_required_stream_files,
    expected_stream_names,
    extract_stream_file_map,
)


def test_expected_stream_names_uses_configured_unique_streams():
    cfg = {
        "capture": {
            "streams": [
                {"name": "rgb"},
                {"name": "mask"},
                {"name": "mask"},
                {"name": "depth"},
            ]
        }
    }

    assert expected_stream_names(cfg) == ["rgb", "mask", "depth"]


def test_extract_stream_file_map_prefers_files_json():
    row = {
        "files_json": json.dumps({"rgb": "from_json_rgb.png"}),
        "rgb_file": "legacy_rgb.png",
        "mask_file": "legacy_mask.png",
    }

    assert extract_stream_file_map(row) == {
        "rgb": "from_json_rgb.png",
        "mask": "legacy_mask.png",
    }


def test_check_required_stream_files_reports_missing_and_bad_paths(tmp_path):
    good = tmp_path / "rgb.png"
    good.write_bytes(b"x")

    ok, missing, bad_paths = check_required_stream_files(
        {"rgb": str(good), "mask": str(tmp_path / "missing.png")},
        ["rgb", "mask", "depth"],
    )

    assert ok is False
    assert missing == ["depth"]
    assert bad_paths == {"mask": str(tmp_path / "missing.png")}
