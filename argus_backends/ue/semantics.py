"""Semantic annotation capabilities advertised by the UE backend."""

from argus_core.planning import BackendCapabilities


def default_annotation_capabilities():
    """Describe the current UE CustomStencil implementation accurately."""
    return BackendCapabilities(
        name="ue",
        component_labeling=True,
        material_slot_labeling=False,
        instance_labeling=False,
        proxy_labeling=True,
    )
