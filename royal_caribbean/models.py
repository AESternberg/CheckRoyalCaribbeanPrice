"""Shared runtime and authentication data models for royal_caribbean."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "APIAccess",
]


@dataclass
class APIAccess:
    """Authentication session container holding current digital passport tokens.

    Maintains the server-assigned user 'id', OAuth bearer token strings, and the
    persistent network connection session pool context.
    """

    token: str
    id: str
    session: Any
    loyalty_number: Optional[str] = None
