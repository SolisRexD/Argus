"""Heuristic runtime semantic stencil inference.

This is a fallback for streamed or spawned UE actors that do not yet have a
curated semantic_map.csv rule. Curated non-zero stencil values should still win.
"""

import csv
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSemanticDecision:
    semantic_class: str
    stencil: int
    reason: str


RULES = (
    ("sky", 7, ("skysphere", "sky_sphere", "skybox", "sky dome")),
    ("pedestrian", 22, ("pedestrian", "masscrowd", "crowd", "person", "human", "crowdcharacter")),
    ("vehicle", 6, ("vehicle", "veh", "car", "taxi", "truck", "bus", "wheel", "tire")),
    ("traffic_sign", 8, ("traffic sign", "traffic_sign", "street sign", "streetsign", "signage", "trafficlight", "traffic_light", "signal")),
    ("prop", 20, ("streetlight", "street_light", "lampost", "lamp_post", "lamp post", "trash", "bin", "post", "pole", "hydrant", "mailbox", "meter")),
    ("curb_border", 4, ("curb", "kerb", "curbstone", "kerbstone", "curb stone", "kerb stone")),
    ("sidewalk", 3, ("sidewalk", "walkway", "pavement", "paving")),
    ("road", 2, ("road", "asphalt", "lane", "crosswalk", "zebra", "roadmarking", "road_marking")),
    ("bridge", 11, ("bridge", "overpass")),
    ("water", 1, ("water", "river", "fountain_water")),
    ("fountain", 13, ("fountain", "drinkingfountain")),
    ("building", 5, ("building", "bldg", "facade", "window", "wall", "roof", "door", "cityblock", "hlod", "tower", "glass", "concretefacade")),
    ("tree", 15, ("tree", "trunk", "branch", "foliage", "canopy", "oak", "elm", "maple", "amurcork")),
    ("bush", 16, ("bush", "shrub", "hedge")),
    ("grass", 17, ("grass", "lawn", "turf")),
    ("flowerbed", 18, ("flower", "flowerbed", "planter")),
    ("bench", 9, ("bench",)),
    ("fence", 10, ("fence", "railing", "guardrail")),
    ("manhole", 12, ("manhole",)),
    ("umbrella", 14, ("umbrella",)),
    ("terrain", 19, ("terrain", "landscape", "ground", "dirt", "soil", "rock")),
    ("fx", 21, ("particle", "niagara", "vfx", "splash", "smoke", "steam")),
    ("prop", 20, ("prop", "lamp", "street furniture", "street_furniture")),
)


IGNORE_KEYWORDS = (
    "sc_rgb",
    "sc_mask",
    "sc_depth",
    "sc_normal",
    "sc_debug",
    "scenecapture",
    "scene capture",
    "billboardcomponent",
    "arrowcomponent",
    "drawspherecomponent",
    "postprocessvolume",
    "reflectioncapture",
    "atmosphericfog",
    "directionallight",
    "pointlight",
    "spotlight",
    "rectlight",
    "skylight",
)


def _normalize_text(value):
    text = str(value or "").strip().lower()
    text = re.sub(r"[^0-9a-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalize(fields):
    return " ".join(_normalize_text(field) for field in fields).strip()


def _contains_any(text, keywords):
    tokens = set(text.split())
    padded_text = " {} ".format(text)
    compact_text = text.replace(" ", "")

    for keyword in keywords:
        normalized_keyword = _normalize_text(keyword)

        if not normalized_keyword:
            continue

        keyword_tokens = normalized_keyword.split()

        if len(keyword_tokens) == 1 and normalized_keyword in tokens:
            return str(keyword).strip()

        if len(keyword_tokens) > 1 and " {} ".format(normalized_keyword) in padded_text:
            return str(keyword).strip()

        compact_keyword = normalized_keyword.replace(" ", "")
        if len(compact_keyword) >= 6 and compact_keyword in compact_text:
            return str(keyword).strip()

    return ""


def _parse_int(value, row_number, field_name):
    text = str(value or "").strip()

    if not text:
        raise ValueError("Missing {} in semantic alias row {}".format(field_name, row_number))

    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(
            "Invalid {} {!r} in semantic alias row {}".format(field_name, text, row_number)
        ) from exc


def load_semantic_alias_rules(path):
    """Load plain-text semantic aliases from a CSV file.

    The CSV is intentionally simple so UE users and LLM-assisted workflows can
    edit it without Python or regular expressions. Required columns:
    semantic_class, stencil, aliases. aliases is a semicolon-separated list.
    """
    loaded = []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        required_fields = {"semantic_class", "stencil", "aliases"}
        missing = required_fields - set(reader.fieldnames or [])

        if missing:
            raise ValueError(
                "Semantic alias CSV is missing required columns: {}".format(
                    ", ".join(sorted(missing))
                )
            )

        for row_number, row in enumerate(reader, start=2):
            values = [str(value or "").strip() for value in row.values()]
            if not any(values):
                continue

            semantic_class = str(row.get("semantic_class") or "").strip()
            aliases = tuple(
                alias.strip()
                for alias in str(row.get("aliases") or "").split(";")
                if alias.strip()
            )

            if not semantic_class or not aliases:
                continue

            priority_text = str(row.get("priority") or "").strip()
            priority = _parse_int(priority_text, row_number, "priority") if priority_text else 1000
            stencil = _parse_int(row.get("stencil"), row_number, "stencil")
            loaded.append((priority, len(loaded), semantic_class, stencil, aliases))

    return tuple(
        (semantic_class, stencil, aliases)
        for _, _, semantic_class, stencil, aliases in sorted(
            loaded,
            key=lambda item: (item[0], item[1]),
        )
    )


def infer_semantic_stencil(fields, unknown_for_unmatched=False, rules=None):
    """Infer a semantic stencil from names/classes/material identifiers."""
    text = _normalize(fields)

    if not text.strip():
        return None

    matched = _contains_any(text, IGNORE_KEYWORDS)
    if matched:
        return None

    for semantic_class, stencil, aliases in tuple(rules or ()):
        matched = _contains_any(text, aliases)
        if matched:
            return RuntimeSemanticDecision(
                semantic_class=semantic_class,
                stencil=stencil,
                reason="alias:{}".format(matched),
            )

    for semantic_class, stencil, keywords in RULES:
        matched = _contains_any(text, keywords)
        if matched:
            return RuntimeSemanticDecision(
                semantic_class=semantic_class,
                stencil=stencil,
                reason="keyword:{}".format(matched),
            )

    if unknown_for_unmatched:
        return RuntimeSemanticDecision(
            semantic_class="unknown",
            stencil=250,
            reason="unmatched",
        )

    return None
