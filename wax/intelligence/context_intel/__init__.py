"""
Context Intelligence — model-guided investigation over existing Waza systems.
"""

from wax.intelligence.context_intel.brief import ContextBrief, brief_to_tutor_text

def investigate_context(*args, **kwargs):
    from wax.intelligence.context_intel.agent import investigate_context as _inv
    return _inv(*args, **kwargs)

__all__ = [
    "ContextBrief",
    "brief_to_tutor_text",
    "investigate_context",
]
