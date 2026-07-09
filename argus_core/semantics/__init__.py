"""Engine-independent semantic stencil inference."""

from .auto_stencil import RuntimeSemanticDecision, infer_semantic_stencil, load_semantic_alias_rules

__all__ = [
    "RuntimeSemanticDecision",
    "infer_semantic_stencil",
    "load_semantic_alias_rules",
]
