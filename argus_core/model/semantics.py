"""Semantic class model independent from any rendering backend."""

from dataclasses import dataclass
from enum import Enum


class SemanticClassKind(str, Enum):
    """Known semantic class roles."""

    NORMAL = "normal"
    BACKGROUND = "background"
    UNKNOWN = "unknown"
    IGNORE = "ignore"


@dataclass(frozen=True)
class ColorRGB:
    """RGB color stored in 0-255 integer space."""

    r: int
    g: int
    b: int

    @classmethod
    def from_values(cls, r, g, b):
        return cls(_clamp_u8(r), _clamp_u8(g), _clamp_u8(b))


@dataclass(frozen=True)
class SemanticClass:
    """A semantic class with an optional render-backend stencil mapping."""

    name: str
    stencil: int
    color_rgb: ColorRGB
    kind: SemanticClassKind = SemanticClassKind.NORMAL

    @classmethod
    def from_row(cls, row):
        name = _text(row.get("semantic_class"))
        lower = name.lower()
        kind = SemanticClassKind.NORMAL

        if lower == "background":
            kind = SemanticClassKind.BACKGROUND
        elif lower == "unknown":
            kind = SemanticClassKind.UNKNOWN
        elif lower == "ignore":
            kind = SemanticClassKind.IGNORE

        return cls(
            name=name,
            stencil=_int(row.get("stencil"), 0),
            color_rgb=ColorRGB.from_values(
                row.get("color_r", 0),
                row.get("color_g", 0),
                row.get("color_b", 0),
            ),
            kind=kind,
        )


def _text(value):
    return str(value or "").strip()


def _int(value, default):
    try:
        text = str(value).strip()
        if not text:
            return default
        return int(text)
    except Exception:
        return default


def _clamp_u8(value):
    try:
        number = int(float(value))
    except Exception:
        number = 0
    return max(0, min(255, number))
