"""Durable leased contribution tasks. Submissions are untrusted until accepted.

SQLite serializes claims and fences expired workers. This is a single database
coordination service, not a geographically replicated consensus implementation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class CoordinationError(ValueError):
    pass


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_encode(value).encode()).hexdigest()


class CoordinationQueue:
    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            if columns and "unresolved_prerequisites" not in columns:
                raise CoordinationError("incompatible coordination schema; use a fresh queue database")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
                    priority INTEGER NOT NULL, max_attempts INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    fence INTEGER NOT NULL DEFAULT 0, worker TEXT, lease_until REAL,
                    submission TEXT, submission_sha256 TEXT, result TEXT, error TEXT,
                    unresolved_prerequisites INTEGER NOT NULL DEFAULT 0 CHECK(unresolved_prerequisites>=0),
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS prerequisites (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    prerequisite_id TEXT NOT NULL REFERENCES tasks(id),
                    PRIMARY KEY(task_id, prerequisite_id)
                );
                CREATE INDEX IF NOT EXISTS task_ready_priority ON tasks(priority DESC, created, id)
                    WHERE state='pending' AND unresolved_prerequisites=0;
                CREATE INDEX IF NOT EXISTS prerequisite_dependents ON prerequisites(prerequisite_id, task_id);
                CREATE INDEX IF NOT EXISTS task_lease_expiry ON tasks(state, lease_until);
            """)

    @contextmanager
    def _db(self, write=False):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _task(db, task_id):
        row = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise CoordinationError(f"unknown task: {task_id}")
        task = dict(row)
        for field in ("payload", "submission", "result"):
            if task[field] is not None:
                task[field] = json.loads(task[field])
        task["prerequisites"] = [r[0] for r in db.execute(
            "SELECT prerequisite_id FROM prerequisites WHERE task_id=? ORDER BY prerequisite_id", (task_id,))]
        return task

    def enqueue(self, payload, task_id=None, prerequisites=(), priority=0, max_attempts=3):
        with self._db(True) as db:
            return self._enqueue(db, payload, task_id, prerequisites, priority, max_attempts)

    def enqueue_many(self, tasks):
        """Atomically enqueue at most 1000 immutable task records.

        Each record accepts the same arguments as enqueue. A later invalid
        record rolls back the whole batch, including prerequisite inserts.
        """
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 1000:
            raise CoordinationError("enqueue_many requires 1 to 1000 records")
        if any(not isinstance(task, dict) for task in tasks):
            raise CoordinationError("batch tasks must be objects")
        with self._db(True) as db:
            return [self._enqueue(db, **task) for task in tasks]

    def _enqueue(self, db, payload, task_id=None, prerequisites=(), priority=0, max_attempts=3):
        if not isinstance(payload, dict):
            raise CoordinationError("task payload must be an object")
        encoded = _encode(payload)
        if len(encoded.encode()) > 1_000_000:
            raise CoordinationError("task payload exceeds 1 MB")
        digest = _digest(payload)
        task_id = task_id or digest
        if not isinstance(task_id, str) or not task_id or len(task_id) > 256:
            raise CoordinationError("invalid task id")
        if type(priority) is not int or not -(2**63) <= priority < 2**63 or type(max_attempts) is not int or not 1 <= max_attempts <= 1000:
            raise CoordinationError("invalid priority or attempt budget")
        if not isinstance(prerequisites, (tuple, list)) or len(prerequisites) > 1000 or any(not isinstance(d, str) for d in prerequisites):
            raise CoordinationError("invalid prerequisites")
        deps = sorted(set(prerequisites))
        if task_id in deps:
            raise CoordinationError("invalid or cyclic prerequisites")
        if db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone():
            old = self._task(db, task_id)
            if (old["payload_sha256"], old["prerequisites"], old["priority"], old["max_attempts"]) != (digest, deps, priority, max_attempts):
                raise CoordinationError("task id already binds different immutable work")
            return old
        unresolved = sum(self._task(db, dependency)["state"] != "accepted" for dependency in deps)
        # Existing prerequisites are immutable and new IDs cannot already be
        # referenced, so accepting only existing IDs constructs a DAG.
        db.execute("INSERT INTO tasks(id,payload,payload_sha256,priority,max_attempts,created,unresolved_prerequisites) VALUES(?,?,?,?,?,?,?)",
                   (task_id, encoded, digest, priority, max_attempts, time.time(), unresolved))
        db.executemany("INSERT INTO prerequisites VALUES(?,?)", [(task_id, d) for d in deps])
        return self._task(db, task_id)

    @staticmethod
    def _duration(seconds):
        if isinstance(seconds, bool) or not isinstance(seconds, (float, int)) or not 0 < seconds <= 86400:
            raise CoordinationError("lease duration must be positive and at most one day")
        return seconds

    def claim(self, worker, lease_seconds=60):
        self._duration(lease_seconds)
        if not isinstance(worker, str) or not worker or len(worker) > 256:
            raise CoordinationError("invalid worker identity")
        now = time.time()
        with self._db(True) as db:
            db.execute("UPDATE tasks SET state=CASE WHEN attempts>=max_attempts THEN 'failed' ELSE 'pending' END, error='lease expired', worker=NULL, lease_until=NULL WHERE state='leased' AND lease_until<=?", (now,))
            row = db.execute("""SELECT t.id FROM tasks t INDEXED BY task_ready_priority WHERE t.state='pending'
                AND t.unresolved_prerequisites=0
                ORDER BY t.priority DESC,t.created,t.id LIMIT 1""").fetchone()
            if row is None:
                return None
            previous_error = self._task(db, row[0])["error"]
            db.execute("UPDATE tasks SET state='leased',attempts=attempts+1,fence=fence+1,worker=?,lease_until=?,submission=NULL,submission_sha256=NULL,error=NULL WHERE id=?",
                       (worker, now + lease_seconds, row[0]))
            result = self._task(db, row[0])
            result["previous_error"] = previous_error
            return result

    @staticmethod
    def _lease(task, worker, fence):
        if type(fence) is not int or task["state"] != "leased" or task["worker"] != worker or task["fence"] != fence or task["lease_until"] <= time.time():
            raise CoordinationError("stale, expired, or foreign lease")

    def renew(self, task_id, worker, fence, lease_seconds=60):
        self._duration(lease_seconds)
        with self._db(True) as db:
            task = self._task(db, task_id)
            self._lease(task, worker, fence)
            db.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time()+lease_seconds, task_id))
            return self._task(db, task_id)

    def submit(self, task_id, worker, fence, submission):
        if not isinstance(submission, dict) or len(_encode(submission).encode()) > 1_000_000:
            raise CoordinationError("submission must be an object of at most 1 MB")
        digest = _digest(submission)
        with self._db(True) as db:
            task = self._task(db, task_id)
            if task["state"] in {"submitted", "accepted"} and task["worker"] == worker and task["fence"] == fence and task["submission_sha256"] == digest:
                return task
            self._lease(task, worker, fence)
            db.execute("UPDATE tasks SET state='submitted',submission=?,submission_sha256=?,lease_until=NULL WHERE id=?", (_encode(submission), digest, task_id))
            return self._task(db, task_id)

    def accept(self, task_id, submission_sha256=None, result=None):
        if len(_encode(result).encode()) > 1_000_000:
            raise CoordinationError("acceptance result exceeds 1 MB")
        with self._db(True) as db:
            task = self._task(db, task_id)
            if submission_sha256 is None or task["submission_sha256"] != submission_sha256:
                raise CoordinationError("acceptance requires the exact evaluated submission hash")
            if task["state"] == "accepted" and task["result"] == result:
                return task
            if task["state"] != "submitted":
                raise CoordinationError("only submitted work can be accepted")
            db.execute("UPDATE tasks SET state='accepted',result=? WHERE id=?", (_encode(result), task_id))
            # Acceptance is irreversible. This executes only on the first
            # transition, and the reverse index touches direct dependents only.
            db.execute("UPDATE tasks SET unresolved_prerequisites=unresolved_prerequisites-1 WHERE id IN (SELECT task_id FROM prerequisites WHERE prerequisite_id=?)", (task_id,))
            return self._task(db, task_id)

    def reject(self, task_id, reason, retry=True, submission_sha256=None):
        with self._db(True) as db:
            task = self._task(db, task_id)
            if task["state"] != "submitted" or submission_sha256 != task["submission_sha256"]:
                raise CoordinationError("rejection requires the exact submitted work hash")
            state = "pending" if retry and task["attempts"] < task["max_attempts"] else "failed"
            db.execute("UPDATE tasks SET state=?,error=? WHERE id=?", (state, str(reason)[:10000], task_id))
            return self._task(db, task_id)

    def get(self, task_id):
        with self._db() as db:
            return self._task(db, task_id)

    def list_tasks(self, state=None, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise CoordinationError("invalid pagination")
        if state is not None and state not in {"pending", "leased", "submitted", "accepted", "failed"}:
            raise CoordinationError("unknown state")
        with self._db() as db:
            rows = db.execute("SELECT id FROM tasks WHERE (? IS NULL OR state=?) ORDER BY created,id LIMIT ? OFFSET ?", (state,state,limit,offset))
            result = []
            size = 0
            for row in rows:
                task = self._task(db, row[0])
                task_size = len(_encode(task).encode())
                if result and size + task_size > 8_000_000:
                    break
                result.append(task)
                size += task_size
            return result


def coordination_server(queue, tokens, host="127.0.0.1", port=0):
    """Return an unstarted server. tokens maps secret -> {role, worker?}.

    Transport encryption belongs to a TLS terminator; bind to loopback by default.
    Worker credentials bind claims to one identity and cannot accept submissions.
    """
    if not tokens or any(not isinstance(key, str) or len(key) < 16 for key in tokens):
        raise CoordinationError("use explicit credentials of at least 16 characters")
    credentials = dict(tokens)
    for policy in credentials.values():
        if policy.get("role") not in {"admin", "worker"} or (policy["role"] == "worker" and not policy.get("worker")):
            raise CoordinationError("invalid credential policy")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def do_POST(self):
            supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
            policy = next((p for token,p in credentials.items() if hmac.compare_digest(token.encode(), supplied.encode())), None)
            if policy is None:
                self.reply(401, {"error":"unauthorized"})
                return
            operation = self.path.removeprefix("/")
            allowed = {"enqueue", "accept", "reject", "get", "list_tasks", "claim", "renew", "submit"} if policy["role"] == "admin" else {"claim", "renew", "submit", "get"}
            if operation not in allowed:
                self.reply(403, {"error":"operation forbidden"})
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0 or size > 1_100_000 or self.headers.get("Transfer-Encoding"):
                    self.reply(413, {"error":"body too large or unsupported encoding"})
                    return
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise CoordinationError("request must be an object")
                if policy["role"] == "worker":
                    worker = policy["worker"]
                    if operation == "get":
                        if queue.get(data.get("task_id"))["worker"] != worker:
                            self.reply(403, {"error":"task belongs to another worker"})
                            return
                    else:
                        if "worker" in data and data["worker"] != worker:
                            self.reply(403, {"error":"worker identity mismatch"})
                            return
                        data["worker"] = worker
                result = getattr(queue, operation)(**data)
                self.reply(200, {"result":result})
            except (ValueError, TypeError, KeyError) as error:
                self.reply(400, {"error":str(error)})

        def reply(self, status, payload):
            body = _encode(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


class CoordinationClient:
    def __init__(self, url, token, timeout=30):
        self.url, self.token, self.timeout = url.rstrip("/"), token, timeout

    def call(self, operation, **kwargs):
        if operation not in {"enqueue", "accept", "reject", "get", "list_tasks", "claim", "renew", "submit"}:
            raise CoordinationError("unknown operation")
        request = Request(self.url+"/"+operation, data=_encode(kwargs).encode(), headers={"Authorization":"Bearer "+self.token,"Content-Type":"application/json"}, method="POST")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(16_000_001)
                if len(raw) > 16_000_000:
                    raise CoordinationError("response too large; reduce page size")
                return json.loads(raw)["result"]
        except HTTPError as error:
            detail = error.read(10000).decode("utf-8", errors="replace")
            raise CoordinationError(f"coordination request failed ({error.code}): {detail}") from None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda **kwargs: self.call(name, **kwargs)
