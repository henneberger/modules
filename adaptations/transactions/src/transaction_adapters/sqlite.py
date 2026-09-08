"""Trusted ordinary Python implementation of an opaque transaction resource."""

import sqlite3


class Session:
    """Private adapter representation; clients only receive an opaque handle."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            "CREATE TABLE records (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.connection.execute("BEGIN")
        self.closed = False

    def active(self):
        if self.closed:
            raise RuntimeError("transaction session has already been consumed")
        return self.connection

    def close(self):
        self.closed = True
        self.connection.close()


def create():
    """Construct the operation exports without opening a database yet."""

    def begin():
        return Session()

    def read(session, key):
        row = session.active().execute(
            "SELECT value FROM records WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            raise KeyError(key)
        return row[0]

    def put(session, key, value):
        session.active().execute(
            "INSERT INTO records (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def commit(session):
        connection = session.active()
        try:
            connection.commit()
            return "committed"
        finally:
            session.close()

    def abort(session):
        connection = session.active()
        try:
            connection.rollback()
        finally:
            session.close()

    return {
        "begin": begin,
        "read": read,
        "put": put,
        "commit": commit,
        "abort": abort,
    }
