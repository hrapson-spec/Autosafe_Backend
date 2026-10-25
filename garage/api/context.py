from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .config import Settings
from .db import Database
from .dvsa import DvsaClient
from .mailer import EmailSender
from .provider import AssessmentProvider
from .scope import Scope
from .store import Scoped
from .timeutil import utcnow


@dataclass
class Ctx:
    db: Database
    settings: Settings
    dvsa: DvsaClient               # already throttled by the app factory
    provider: AssessmentProvider
    mailer: EmailSender
    clock: Callable[[], datetime] = utcnow

    @contextlib.contextmanager
    def scoped(self, scope: Scope):
        """The one way to reach workshop-private rows: a transaction bound to a Scope."""
        with self.db.tx() as cur:
            yield Scoped(cur, scope)
