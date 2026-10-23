"""本地存储：SQLite。岗位池、投递记录、状态机流转、匹配报告缓存。

为什么用 SQLite 而不是纯文件：
状态机的合法转移需要事务保证；投递历史需要按时间查询。
数据量很小，但一致性要求高，单文件库是最轻的方案。
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..config import DB_PATH, ensure_dirs
from ..models import (
    AppStatus,
    Application,
    Job,
    JobKind,
    MatchReport,
    Snapshot,
    StatusEvent,
    can_transition,
    slugify,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    org TEXT,
    mentor TEXT,
    url TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    app_id TEXT NOT NULL,
    status TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS matches (
    job_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_job ON applications(job_id);
CREATE INDEX IF NOT EXISTS idx_evt_app ON events(app_id);
"""


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    try:
        c.executescript(SCHEMA)
        yield c
        c.commit()
    finally:
        c.close()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ------------------------------------------------------------------- jobs

def save_job(job: Job) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO jobs(id,kind,title,org,mentor,url,payload,created_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,title=excluded.title,org=excluded.org,"
            "mentor=excluded.mentor,url=excluded.url,payload=excluded.payload",
            (
                job.id,
                job.kind.value,
                job.title,
                job.org,
                job.mentor,
                job.url,
                json.dumps(job.model_dump(mode="json"), ensure_ascii=False),
                job.created_at,
            ),
        )


def get_job(job_id: str) -> Job | None:
    with conn() as c:
        row = c.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    return Job(**json.loads(row["payload"])) if row else None


def list_jobs(kind: JobKind | None = None) -> list[Job]:
    sql = "SELECT payload FROM jobs"
    args: tuple = ()
    if kind:
        sql += " WHERE kind=?"
        args = (kind.value,)
    sql += " ORDER BY created_at DESC"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [Job(**json.loads(r["payload"])) for r in rows]


# ------------------------------------------------------------ applications

def create_application(job: Job) -> Application:
    app_id = f"app_{slugify(job.org or job.mentor or job.title, 16)}_{int(time.time()) % 100000}"
    app = Application(
        id=app_id,
        job_id=job.id,
        kind=job.kind,
        status=AppStatus.DRAFT,
        history=[StatusEvent(status=AppStatus.DRAFT, note="建档")],
    )
    with conn() as c:
        c.execute(
            "INSERT INTO applications(id,job_id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (app.id, app.job_id, app.kind.value, app.status.value,
             json.dumps(app.model_dump(mode="json"), ensure_ascii=False), _now(), _now()),
        )
        c.execute(
            "INSERT INTO events(app_id,status,at,note) VALUES(?,?,?,?)",
            (app.id, AppStatus.DRAFT.value, _now(), "建档"),
        )
    return app


def get_application(app_id: str) -> Application | None:
    with conn() as c:
        row = c.execute("SELECT payload FROM applications WHERE id=?", (app_id,)).fetchone()
    return Application(**json.loads(row["payload"])) if row else None


def find_application_by_job(job_id: str) -> Application | None:
    with conn() as c:
        row = c.execute(
            "SELECT payload FROM applications WHERE job_id=? ORDER BY created_at DESC LIMIT 1", (job_id,)
        ).fetchone()
    return Application(**json.loads(row["payload"])) if row else None


def list_applications(status: AppStatus | None = None) -> list[Application]:
    sql = "SELECT payload FROM applications"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status.value,)
    sql += " ORDER BY updated_at DESC"
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [Application(**json.loads(r["payload"])) for r in rows]


def transition(app_id: str, to_status: AppStatus, note: str = "") -> Application:
    app = get_application(app_id)
    if app is None:
        raise ValueError(f"投递记录不存在：{app_id}")
    if app.status == to_status:
        return app
    if not can_transition(app.kind, app.status, to_status):
        allowed = sorted(s.value for s in _reachable(app.kind, app.status))
        raise ValueError(f"非法状态转移：{app.status.value} → {to_status.value}。当前可去：{allowed}")
    app.status = to_status
    app.history.append(StatusEvent(status=to_status, note=note))
    with conn() as c:
        c.execute(
            "UPDATE applications SET status=?, payload=?, updated_at=? WHERE id=?",
            (to_status.value, json.dumps(app.model_dump(mode="json"), ensure_ascii=False), _now(), app_id),
        )
        c.execute(
            "INSERT INTO events(app_id,status,at,note) VALUES(?,?,?,?)",
            (app_id, to_status.value, _now(), note),
        )
    return app


def _reachable(kind: JobKind, status: AppStatus) -> set[AppStatus]:
    from ..models import TRANSITIONS

    return TRANSITIONS.get(kind, {}).get(status, set())


def set_snapshot(app_id: str, snapshot: Snapshot) -> None:
    app = get_application(app_id)
    if app is None:
        raise ValueError(f"投递记录不存在：{app_id}")
    app.snapshot = snapshot
    with conn() as c:
        c.execute(
            "UPDATE applications SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(app.model_dump(mode="json"), ensure_ascii=False), _now(), app_id),
        )


def app_timeline(app_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute("SELECT status, at, note FROM events WHERE app_id=? ORDER BY seq", (app_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------- matches

def save_match(report: MatchReport) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO matches(job_id,payload,generated_at) VALUES(?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET payload=excluded.payload, generated_at=excluded.generated_at",
            (report.job_id, json.dumps(report.model_dump(mode="json"), ensure_ascii=False), report.generated_at),
        )


def get_match(job_id: str) -> MatchReport | None:
    with conn() as c:
        row = c.execute("SELECT payload FROM matches WHERE job_id=?", (job_id,)).fetchone()
    return MatchReport(**json.loads(row["payload"])) if row else None


def stats() -> dict:
    with conn() as c:
        jobs = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        apps = c.execute("SELECT COUNT(*) n FROM applications").fetchone()["n"]
        by_status = {
            r["status"]: r["n"]
            for r in c.execute("SELECT status, COUNT(*) n FROM applications GROUP BY status").fetchall()
        }
    return {"jobs": jobs, "applications": apps, "by_status": by_status}
