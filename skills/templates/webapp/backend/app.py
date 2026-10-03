"""可编辑数据展示的最小后端。复制到交付目录后按业务表扩展，不要从零重写。"""
import csv
import io
import sqlite3
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "app.db"
FRONTEND = ROOT / "frontend"

app = FastAPI(title="data-admin")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL DEFAULT '',
                amount REAL,
                note TEXT NOT NULL DEFAULT '',
                raw_text TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS edit_log (
                id INTEGER PRIMARY KEY,
                record_id INTEGER NOT NULL,
                field TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                ts TEXT NOT NULL
            );
            """
        )
        n = conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
        if n == 0:
            now = datetime.now().isoformat(timespec="seconds")
            conn.executemany(
                "INSERT INTO records (name, category, amount, note, raw_text, updated_at) VALUES (?,?,?,?,?,?)",
                [
                    ("示例甲", "A", 12.5, "可编辑", "原文甲", now),
                    ("示例乙", "B", 3, "可编辑", "原文乙", now),
                    ("示例丙", "A", 20, "可编辑", "原文丙", now),
                ],
            )


class Patch(BaseModel):
    name: str = Field(min_length=1)
    category: str = ""
    amount: float | None = None
    note: str = ""


EDITABLE = ("name", "category", "amount", "note")


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/api/records")
def list_records(q: str = "", category: str = "", sort: str = "id", desc: int = 0, page: int = 1, size: int = 20):
    if sort not in ("id", "name", "category", "amount"):
        sort = "id"
    order = "DESC" if desc else "ASC"
    where, args = [], []
    if q:
        where.append("(name LIKE ? OR note LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])
    if category:
        where.append("category = ?")
        args.append(category)
    sql = "SELECT * FROM records"
    if where:
        sql += " WHERE " + " AND ".join(where)
    with connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM ({sql})", args).fetchone()["n"]
        rows = conn.execute(
            sql + f" ORDER BY {sort} {order} LIMIT ? OFFSET ?",
            args + [max(1, min(size, 200)), max(0, (page - 1) * size)],
        ).fetchall()
        cats = [r["category"] for r in conn.execute("SELECT DISTINCT category FROM records ORDER BY category")]
    return {"total": total, "categories": cats, "items": [dict(r) for r in rows]}


@app.get("/api/records/{rid}")
def get_record(rid: int):
    with connect() as conn:
        row = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        if row is None:
            raise HTTPException(404, "not found")
        logs = conn.execute(
            "SELECT * FROM edit_log WHERE record_id=? ORDER BY id DESC LIMIT 20", (rid,)
        ).fetchall()
    return {"record": dict(row), "logs": [dict(r) for r in logs]}


@app.patch("/api/records/{rid}")
def patch_record(rid: int, body: Patch):
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as conn:
        old = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        if old is None:
            raise HTTPException(404, "not found")
        for field in EDITABLE:
            new = getattr(body, field)
            prev = old[field]
            if str(prev) == str(new):
                continue
            conn.execute(
                "INSERT INTO edit_log (record_id, field, old_value, new_value, ts) VALUES (?,?,?,?,?)",
                (rid, field, "" if prev is None else str(prev), "" if new is None else str(new), now),
            )
        conn.execute(
            "UPDATE records SET name=?, category=?, amount=?, note=?, updated_at=? WHERE id=?",
            (body.name, body.category, body.amount, body.note, now, rid),
        )
    return get_record(rid)


@app.get("/api/stats")
def stats():
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, ROUND(SUM(amount), 2) AS total, COUNT(*) AS n FROM records GROUP BY category ORDER BY category"
        ).fetchall()
    return {"by_category": [dict(r) for r in rows]}


@app.get("/api/export")
def export_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["id", "name", "category", "amount", "note", "raw_text"])
    with connect() as conn:
        for row in conn.execute("SELECT id, name, category, amount, note, raw_text FROM records ORDER BY id"):
            writer.writerow([row[k] for k in row.keys()])
    return Response(
        "\ufeff" + buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=records.csv"},
    )


if FRONTEND.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND), html=True), name="frontend")
