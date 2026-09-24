"""Ephemeral AI-directed web publication surfaces for WAX Prep."""

from wax.publication.schema import PublicationDocument, Block, BlockType, SCHEMA_VERSION
from wax.publication.renderer import RENDERER_VERSION

__all__ = [
    "PublicationDocument",
    "Block",
    "BlockType",
    "SCHEMA_VERSION",
    "RENDERER_VERSION",
]

def __getattr__(name: str):
    if name == "PublicationService":
        from wax.publication.service import PublicationService
        return PublicationService
    raise AttributeError(name)
