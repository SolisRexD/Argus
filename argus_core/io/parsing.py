"""Primitive parsing and normalization helpers."""

from datetime import datetime


def now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def parse_int(value, default=None):
    try:
        if value is None or not str(value).strip():
            return default
        return int(value)
    except Exception:
        return default


def parse_float(value, default=None):
    try:
        if value is None or not str(value).strip():
            return default
        return float(value)
    except Exception:
        return default


def normalize_color_255_to_1(value):
    number = float(value)
    if number > 1.0:
        return max(0.0, min(1.0, number / 255.0))
    return max(0.0, min(1.0, number))
