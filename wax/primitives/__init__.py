"""
WAX infrastructure primitives available to the AI.

These are general capabilities, not educational workflows.
The AI decides when and how to use them.
"""

from wax.primitives.registry import PRIMITIVE_SPECS, execute_primitive, list_primitive_specs

__all__ = [
    "PRIMITIVE_SPECS",
    "execute_primitive",
    "list_primitive_specs",
]
