"""Student World — persistent isolated environment per principal."""

from wax.world.manager import World, create_world, get_or_create_world
from wax.world.errors import WorldError

__all__ = ["World", "create_world", "get_or_create_world", "WorldError"]
