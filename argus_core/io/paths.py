"""Filesystem paths used by Argus core and application layers."""

import os


def get_project_root():
    """Return the source checkout root containing ``argus_core``."""
    module_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(module_dir, "..", ".."))


def resolve_path(path_str, project_root=None):
    """Resolve a configured path relative to an explicit or detected root."""
    if not path_str:
        return ""
    if os.path.isabs(path_str):
        return os.path.normpath(path_str)
    return os.path.normpath(os.path.join(project_root or get_project_root(), path_str))


def ensure_dir(path_str):
    """Create a directory when a non-empty path is provided."""
    if path_str:
        os.makedirs(path_str, exist_ok=True)
