"""Operator tool (R17: the invite-code tool is the only recruitment mechanism).

  python -m garage.api.cli migrate
  python -m garage.api.cli create-invite --note "Smith Motors" [--ttl-days 14]
The code is printed once; only its hash is stored.
"""
from __future__ import annotations

import argparse
import os

from . import auth_store
from .config import Settings
from .db import Database, migrate


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="garage.api.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    c = sub.add_parser("create-invite")
    c.add_argument("--note", default=None)
    c.add_argument("--ttl-days", type=int, default=14)
    a = p.parse_args(argv)
    dsn = Settings.from_env().database_url or os.environ["GARAGE_DATABASE_URL"]
    if a.cmd == "migrate":
        print("applied:", migrate(dsn) or "nothing to do")
    else:
        db = Database(dsn, 1, 1)
        with db.tx() as cur:
            print(auth_store.create_invite(cur, a.note, a.ttl_days))


if __name__ == "__main__":
    main()
