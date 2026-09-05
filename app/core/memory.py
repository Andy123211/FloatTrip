"""用户记忆读写：偏好提取、行程保存、历史查询。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


# ─── 行程保存 / 查询 ─────────────────────────────────────────

def save_itinerary(
    user_id: str,
    plan: dict,
    query: str,
    conn: sqlite3.Connection,
    *,
    parent_id: str | None = None,
    modification_notes: str | None = None,
    planner_state: dict | None = None,
) -> str:
    plan_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    root_id = plan_id
    version = 1
    if parent_id:
        parent = conn.execute(
            "SELECT id,root_id,version FROM itineraries WHERE id=? AND user_id=?",
            (parent_id, user_id),
        ).fetchone()
        if not parent:
            raise ValueError("base itinerary not found or not owned by user")
        root_id = parent["root_id"] or parent["id"]
        version = int(parent["version"] or 1) + 1
    conn.execute(
        """INSERT INTO itineraries
           (id, user_id, parent_id, query, modification_notes,
            destination, start_date, end_date, plan_json, planner_state_json,
            root_id, version, created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            plan_id, user_id, parent_id, query, modification_notes,
            plan.get("destination", ""),
            plan.get("start_date", ""),
            plan.get("end_date", ""),
            json.dumps(plan, ensure_ascii=False),
            json.dumps(planner_state, ensure_ascii=False) if planner_state else None,
            root_id,
            version,
            now,
        ),
    )
    return plan_id


def load_itinerary(plan_id: str, conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id,parent_id,root_id,version,lock_version,plan_json,modification_notes,planner_state_json "
        "FROM itineraries WHERE id=?",
        (plan_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "plan": json.loads(row["plan_json"]),
        "id": row["id"],
        "parent_id": row["parent_id"],
        "root_id": row["root_id"] or row["id"],
        "version": int(row["version"] or 1),
        "lock_version": int(row["lock_version"] or 1),
        "modification_notes": row["modification_notes"],
        "planner_state": json.loads(row["planner_state_json"]) if row["planner_state_json"] else None,
    }


def update_plan_json(
    plan_id: str,
    user_id: str,
    new_plan: dict,
    conn: sqlite3.Connection,
    *,
    expected_lock_version: int,
) -> bool:
    """用乐观锁更新行程；版本已变化时不覆盖其他请求的编辑。"""
    cur = conn.execute(
        "UPDATE itineraries SET plan_json=?,lock_version=lock_version+1 "
        "WHERE id=? AND user_id=? AND lock_version=?",
        (
            json.dumps(new_plan, ensure_ascii=False),
            plan_id,
            user_id,
            expected_lock_version,
        ),
    )
    return cur.rowcount > 0


def list_itineraries(user_id: str, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT id, parent_id, root_id, version, destination, start_date, end_date, created_at
           FROM itineraries WHERE user_id=? ORDER BY created_at DESC LIMIT 50""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_itineraries_page(
    user_id: str,
    conn: sqlite3.Connection,
    *,
    limit: int,
    cursor: tuple[str, str] | None = None,
) -> tuple[list[dict[str, Any]], tuple[str, str] | None]:
    """Return a stable, cursor-paginated history slice ordered newest first."""
    params: list[Any] = [user_id]
    where = "WHERE user_id=?"
    if cursor:
        where += " AND (created_at < ? OR (created_at = ? AND id < ?))"
        params.extend([cursor[0], cursor[0], cursor[1]])
    params.append(limit + 1)
    rows = conn.execute(
        """SELECT id, parent_id, root_id, version, destination, start_date, end_date, created_at
           FROM itineraries """ + where + " ORDER BY created_at DESC, id DESC LIMIT ?",
        params,
    ).fetchall()
    has_more = len(rows) > limit
    items = [dict(row) for row in rows[:limit]]
    next_cursor = None
    if has_more and items:
        tail = items[-1]
        next_cursor = (str(tail["created_at"]), str(tail["id"]))
    return items, next_cursor


def summarize_plan_for_prompt(plan: dict) -> str:
    """提取行程摘要用于 planner 修改模式的 prompt 注入。"""
    lines = [f"目的地：{plan.get('destination', '')}，{plan.get('start_date', '')} 至 {plan.get('end_date', '')}"]
    for day in plan.get("days", []):
        spots = [t["name"] for t in day.get("timeline", []) if t.get("type") == "attraction"]
        lines.append(f"第{day['day']}天（{day.get('date', '')}）：{'、'.join(spots) or '无景点'}")
    return "\n".join(lines)
