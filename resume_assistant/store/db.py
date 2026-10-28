"""本地存储：SQLite。岗位池、投递记录、状态机流转、匹配报告缓存。

为什么用 SQLite 而不是纯文件：
状态机的合法转移需要事务保证；投递历史需要按时间查询。
数据量很小，但一致性要求高，单文件库是最轻的方案。
"""

from __future__ import annotations

import hashlib
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
    """给岗位建档。

    id 里带岗位标题和 job_id 指纹：
    - 只按单位 + 秒级时间戳编号时，同一秒给同一家公司建两条会撞 UNIQUE 约束
    - 标题进 id 之后，产物目录名 `app_某公司_推荐算法实习生_a1b2c3` 也能一眼认出是哪个岗位

    函数本身幂等：同一个 job 已经有记录就返回旧的那条，不重复建档。
    """
    existing = find_application_by_job(job.id)
    if existing is not None:
        return existing

    head = f"{slugify(job.org or job.mentor or job.title, 14)}_{slugify(job.title, 12)}"
    app_id = f"app_{head}_{hashlib.sha1(job.id.encode('utf-8')).hexdigest()[:6]}"
    app = Application(
        id=app_id,
        job_id=job.id,
        kind=job.kind,
        status=AppStatus.DRAFT,
        history=[StatusEvent(status=AppStatus.DRAFT, note="建档")],
    )
    with conn() as c:
        try:
            c.execute(
                "INSERT INTO applications(id,job_id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                (app.id, app.job_id, app.kind.value, app.status.value,
                 json.dumps(app.model_dump(mode="json"), ensure_ascii=False), _now(), _now()),
            )
        except sqlite3.IntegrityError:
            # 上面「先查再插」之间存在窗口：两个线程同时给同一个岗位建档时，
            # 都会查到「没有」，然后都去插入。撞上唯一约束的那个应该返回已有的那条，
            # 而不是把 IntegrityError 抛给调用方（gen 会因此失败）
            row = c.execute(
                "SELECT payload FROM applications WHERE job_id=? ORDER BY created_at DESC LIMIT 1", (job.id,)
            ).fetchone()
            if row is None:
                raise
            return Application(**json.loads(row["payload"]))
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


def status_history_map() -> dict[str, set[str]]:
    """每条投递「到达过」哪些状态。

    看板的漏斗不能用当前状态倒推：一条 draft 就 withdrawn 的记录，
    按当前状态看是「已离开待办」，会被错算成「投出去过」。
    只有 events 表知道它到底有没有真的走到 sent。
    """
    with conn() as c:
        rows = c.execute("SELECT app_id, status FROM events").fetchall()
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["app_id"], set()).add(r["status"])
    return out


def add_note(app_id: str, note: str) -> list[dict]:
    """给投递记一笔（HR 说了什么、约了几点），不改变状态。

    只写 events 表，不写 history：history 的最后一条被用来算「在这个状态停了几天」，
    记一笔备注就把计时清零的话，「投出去十天没动静」的提醒永远不会触发。
    """
    app = get_application(app_id)
    if app is None:
        raise ValueError(f"投递记录不存在：{app_id}")
    text = (note or "").strip()
    if not text:
        raise ValueError("备注不能为空")
    with conn() as c:
        c.execute(
            "INSERT INTO events(app_id,status,at,note) VALUES(?,?,?,?)",
            (app_id, app.status.value, _now(), text),
        )
    return app_timeline(app_id)


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
