"""Learner intelligence — one connected model of the person."""

__all__ = [
    "LearnerModel",
    "build_learner_model",
    "ContextResolver",
    "LearnerContextPack",
]


def __getattr__(name: str):
    if name in ("LearnerModel", "build_learner_model"):
        from wax.learner.model import LearnerModel, build_learner_model

        return {"LearnerModel": LearnerModel, "build_learner_model": build_learner_model}[name]
    if name in ("ContextResolver", "LearnerContextPack"):
        from wax.learner.resolver import ContextResolver, LearnerContextPack

        return {"ContextResolver": ContextResolver, "LearnerContextPack": LearnerContextPack}[name]
    raise AttributeError(name)
