"""Unity Tether — Python SDK for the Unity Tether bridge."""

from .client import DEFAULT_HOST, DEFAULT_PORT, TetherError, UnityTetherClient

__version__ = "0.1.0"
__all__ = ["UnityTetherClient", "TetherError", "DEFAULT_HOST", "DEFAULT_PORT"]
