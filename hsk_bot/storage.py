"""One private user's state and durable outgoing queue, committed together.

Postgres row locks / SQLite BEGIN IMMEDIATE serialize webhook and cron requests.
No DB connection is kept open between requests (Neon can scale to zero).
"""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def empty_state():
    return {"user": None, "processed": [], "outbox": []}


class Storage:
    def __init__(self, url):
        self.url = url
        self.sqlite = url.startswith("sqlite:///")
        if self.sqlite:
            self.path = url[len("sqlite:///"):]
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connection(self):
        if self.sqlite:
            con = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            try:
                con.execute("BEGIN IMMEDIATE")
                yield con
                con.commit()
            except BaseException:
                con.rollback()
                raise
            finally:
                con.close()
        else:
            import psycopg
            with psycopg.connect(self.url, connect_timeout=15) as con:
                yield con

    def initialize(self):
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS bot_state (id INTEGER PRIMARY KEY, body TEXT NOT NULL)")
            placeholder = "?" if self.sqlite else "%s"
            con.execute(
                f"INSERT INTO bot_state (id, body) VALUES (1, {placeholder}) ON CONFLICT (id) DO NOTHING",
                (json.dumps(empty_state()),),
            )

    def update(self, operation):
        with self.connection() as con:
            lock = "" if self.sqlite else " FOR UPDATE"
            row = con.execute("SELECT body FROM bot_state WHERE id = 1" + lock).fetchone()
            state = json.loads(row[0])
            result = operation(state)
            placeholder = "?" if self.sqlite else "%s"
            con.execute(f"UPDATE bot_state SET body = {placeholder} WHERE id = 1",
                        (json.dumps(state, ensure_ascii=False),))
            return result

    def snapshot(self):
        return self.update(lambda state: json.loads(json.dumps(state)))
