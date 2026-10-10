"""Offline image checks. Pillow/numpy are needed here, never in UE Python."""
import numpy as np
from PIL import Image


def inspect_pair(rgb_path, mask_path, expected_size, allowed_ids):
    errors = []
    with Image.open(rgb_path) as image:
        if image.format != "PNG" or image.size != tuple(expected_size):
            errors.append("rgb_format_or_size")
        rgb = np.asarray(image.convert("RGBA"))
    with Image.open(mask_path) as image:
        if image.format != "PNG" or image.size != tuple(expected_size):
            errors.append("mask_format_or_size")
        mask = np.asarray(image.convert("RGBA"))
    ids = mask[:, :, 0]
    if np.any(mask[:, :, :3] != ids[:, :, None]):
        errors.append("mask_channels_differ")
    if np.any(mask[:, :, 3] != 255) or np.any(rgb[:, :, 3] != 255):
        errors.append("nonopaque_alpha")
    values, counts = np.unique(ids, return_counts=True)
    histogram = dict(zip(map(int, values), map(int, counts)))
    if set(histogram) - set(allowed_ids):
        errors.append("unexpected_class_ids")
    foreground = 1 - histogram.get(0, 0) / ids.size
    if foreground < 0.05:
        errors.append("foreground_below_5_percent")
    if len(set(histogram) - {0}) < 2:
        errors.append("fewer_than_two_visible_classes")
    std = float(rgb[:, :, :3].std())
    if std < 2:
        errors.append("nearly_constant_rgb")
    return {"accepted": not errors, "errors": errors, "class_histogram": histogram,
            "foreground_fraction": foreground, "rgb_std": std,
            "rgb_mean": float(rgb[:, :, :3].mean()),
            "rgb_dark_fraction": float(np.mean(np.max(rgb[:, :, :3], axis=2) < 4)),
            "rgb_bright_fraction": float(np.mean(np.min(rgb[:, :, :3], axis=2) > 251))}
