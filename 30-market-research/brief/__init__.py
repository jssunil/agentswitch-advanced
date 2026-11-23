"""Seat 30 market research brief."""
import importlib


def optional_module(name):
    """brief.<name>, or None only if that module's file doesn't exist yet.

    Any other import failure (a typo, a missing dependency inside the module) is raised, so a
    broken module shows up as a failure instead of looking "not built yet"."""
    full = f"{__name__}.{name}"
    try:
        return importlib.import_module(full)
    except ModuleNotFoundError as e:
        if e.name == full:
            return None
        raise
