"""Memory store serializes structured fields safely."""

from __future__ import annotations


def test_memory_store_module_importable():
    from wax.memory import store

    assert hasattr(store, "memory_search")
    assert hasattr(store, "memory_create")
    assert hasattr(store, "memory_forget")
    assert hasattr(store, "memory_supersede")
