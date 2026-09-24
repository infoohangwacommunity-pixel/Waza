"""Ephemeral AI-directed web publication surfaces for WAX Prep."""

from wax.publication.schema import PublicationDocument, Node, NodeType, SCHEMA_VERSION
from wax.publication.renderer import RENDERER_VERSION

__all__ = [
    "PublicationDocument",
    "Node",
    "NodeType",
    "SCHEMA_VERSION",
    "RENDERER_VERSION",
]


def __getattr__(name: str):
    if name == "PublicationService":
        from wax.publication.service import PublicationService
        return PublicationService
    if name == "Block":
        from wax.publication.schema import Block
        return Block
    if name == "BlockType":
        from wax.publication.schema import BlockType
        return BlockType
    raise AttributeError(name)
