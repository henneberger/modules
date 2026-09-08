"""Small bindings to existing libraries; no replacement retrieval algorithms."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from threading import RLock

import numpy as np
from sklearn.feature_extraction.text import HashingVectorizer


def hashing():
    """Local lexical vectors from scikit-learn; no learned-model claim."""
    vectorizer = HashingVectorizer(n_features=1024, alternate_sign=False, norm="l2")

    def embed(texts):
        return vectorizer.transform(texts).toarray()

    return {"embed": embed}


def cached_embedding(*, base):
    """Apply the standard-library bounded cache to one embedding provider."""

    @lru_cache(maxsize=256)
    def one(text):
        return base.embed([text])[0]

    def embed(texts):
        return np.stack([one(text) for text in texts])

    return {"embed": embed}


def sqlite_documents():
    """A fresh, explicitly closable SQLite database for each module instance."""
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    guard = RLock()
    connection.execute(
        "CREATE TABLE documents (id TEXT PRIMARY KEY, text TEXT NOT NULL)"
    )

    def load(documents):
        with guard, connection:
            connection.execute("DELETE FROM documents")
            connection.executemany(
                "INSERT INTO documents VALUES (?, ?)", sorted(documents.items())
            )

    def read():
        with guard:
            return dict(
                connection.execute("SELECT id, text FROM documents ORDER BY id")
            )

    def close():
        with guard:
            connection.close()

    return {"load": load, "read": read, "close": close}


def retrieval(*, embedding, data, ranker):
    """Translate store rows and vectors to Mari's existing MMR boundary."""

    def search(query, limit):
        documents = data.read()
        if not documents:
            return []
        ids = list(documents)
        vectors = embedding.embed([query, *documents.values()])
        by_id = dict(zip(ids, vectors[1:], strict=True))
        relevance = {key: float(vector @ vectors[0]) for key, vector in by_id.items()}
        hits = ranker.rank(
            relevance,
            lambda a, b: float(by_id[a] @ by_id[b]),
            limit=limit,
            relevance_weight=0.7,
        )
        return [
            {
                "id": hit.document_id,
                "text": documents[hit.document_id],
                "relevance": hit.relevance,
            }
            for hit in hits
        ]

    return {"search": search}


def threaded_queries(*, retrieval, data):
    """Bounded local execution harness using the standard Python executor."""
    guard = RLock()

    def run(documents, queries, limit):
        # One dataset per batch; serialize batches sharing this store instance.
        with guard:
            data.load(documents)
            with ThreadPoolExecutor(max_workers=4) as executor:
                return list(
                    executor.map(lambda query: retrieval.search(query, limit), queries)
                )

    return {"run": run}
