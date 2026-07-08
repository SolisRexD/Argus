"""Engine-independent semantic stencil inference."""

from .auto_stencil import RuntimeSemanticDecision, infer_semantic_stencil

__all__ = [
    "RuntimeSemanticDecision",
    "infer_semantic_stencil",
]
