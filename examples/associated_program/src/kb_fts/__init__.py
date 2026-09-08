"""Small trusted facades over SQLite FTS5, used by the generic module example."""

from __future__ import annotations

import sqlite3

DOCUMENTS = [
    (
        "retention-2026",
        "Data retention",
        3,
        "Customer records must follow the data retention schedule.",
    ),
    (
        "leave-2026",
        "Parental leave",
        7,
        "Employees receive paid parental leave after eligibility review.",
    ),
    (
        "access-2026",
        "Account access",
        2,
        "Managers approve access to restricted customer records.",
    ),
]


def create_parser():
    def prepare(query):
        # Quote a literal phrase in FTS5 query syntax. SQL binding occurs separately.
        return '"' + query.replace('"', '""') + '"'

    return {"prepare": prepare}


def create_index():
    def search(prepared):
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE documents USING fts5(document_id UNINDEXED, title UNINDEXED, page UNINDEXED, text)"
            )
            connection.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?)", DOCUMENTS
            )
            rows = connection.execute(
                "SELECT document_id, title, page, text FROM documents WHERE documents MATCH ? ORDER BY rank, document_id",
                (prepared,),
            ).fetchall()
            return [
                {
                    "document_id": row[0],
                    "title": row[1],
                    "page": int(row[2]),
                    "text": row[3],
                }
                for row in rows
            ]
        finally:
            connection.close()

    return {"search": search}
