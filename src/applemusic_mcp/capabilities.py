"""Permission levels: what this server may reach, fixed by the user at launch.

The level comes from the MCP client config (`serve --level api`), never from
the model. The config tool cannot raise it, so text inside a track or playlist
name cannot either.

  api   Apple's music APIs over the network. Everything this build does needs
        this, so it is the lowest level.
  web   + the Chrome web player (playback and Up Next).
  full  everything.

Unset means unrestricted: the same as full.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

LEVELS = ("api", "web", "full")
ENV_LEVEL = "APPLEMUSIC_LEVEL"

# The `mode` preference values each level may select. There is no native engine
# in this build: playback is the Chrome web player.
MODES_BY_LEVEL = {
    "api": ("auto", "api"),
    "web": ("auto", "api", "chrome", "web"),
    "full": ("auto", "api", "chrome", "web"),
}


class CapabilityDenied(RuntimeError):
    """Raised when a call needs more than the configured level allows."""


@dataclass(frozen=True)
class Level:
    name: str
    web: bool
    explicit: bool


def parse(level: str | None) -> Level:
    """Build a Level from the raw env value. Raises ValueError on an unknown
    level, so a typo stops the server instead of silently allowing everything."""
    if level is None:
        return Level("unrestricted", True, False)
    name = level.strip().lower()
    if name not in LEVELS:
        raise ValueError(f"Unknown level {level!r}. Choose one of: {', '.join(LEVELS)}.")
    return Level(name, web=name in ("web", "full"), explicit=True)


def current() -> Level:
    return parse(os.environ.get(ENV_LEVEL))


def denied_message(feature: str, needs: str) -> str:
    """The refusal text. It tells the user where the level lives; it never
    invites the model to change it."""
    lvl = current()
    return (
        f"{feature} isn't allowed at this server's permission level ({lvl.name}). "
        f"The user chose that level in their MCP client config; to allow this they "
        f"can use --level {needs} there and restart the client."
    )


def require(cap: str, feature: str) -> None:
    """Raise CapabilityDenied unless the level allows ``cap`` ("web")."""
    if cap == "web" and not current().web:
        raise CapabilityDenied(denied_message(feature, "web"))
