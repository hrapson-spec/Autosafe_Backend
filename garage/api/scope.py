"""The workshop scope: the single object that authorises access to private data.

A Scope is created in exactly two places: the `get_scope` auth dependency (from a valid, unrevoked
session whose membership is active) and the job worker (from the workshop_id stored on the job row).
`Scoped` (store.py) cannot be built without one, and every private query it runs is filtered by
`scope.workshop_id`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    workshop_id: str
    membership_id: str | None
    user_id: str | None
    email: str | None
    role: str                      # 'owner' | 'staff' | 'system'
    session_id: str | None = None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @staticmethod
    def system(workshop_id: str) -> "Scope":
        """Background-job principal: bound to the workshop recorded on the job row, nothing else."""
        return Scope(workshop_id=workshop_id, membership_id=None, user_id=None, email=None, role="system")
