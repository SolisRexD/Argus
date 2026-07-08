"""Heuristic runtime semantic stencil inference.

This is a fallback for streamed or spawned UE actors that do not yet have a
curated semantic_map.csv rule. Curated non-zero stencil values should still win.
"""

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
    ("curb_border", 4, ("curb", "kerb")),
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


def _normalize(fields):
    return " ".join(str(field or "").lower() for field in fields)


def _contains_any(text, keywords):
    for keyword in keywords:
        if keyword in text:
            return keyword

    return ""


def infer_semantic_stencil(fields, unknown_for_unmatched=False):
    """Infer a semantic stencil from names/classes/material identifiers."""
    text = _normalize(fields)

    if not text.strip():
        return None

    matched = _contains_any(text, IGNORE_KEYWORDS)
    if matched:
        return None

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
