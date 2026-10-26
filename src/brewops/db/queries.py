"""Read and write queries. All timestamps are naive local time strings."""

import sqlite3
from datetime import date, timedelta
from typing import Any


def _range_clause(column: str, start: str | None, end: str | None) -> tuple[str, list[str]]:
    """Return (conditions, params) limiting `column` to [start, end] inclusive by calendar day.
    start/end are 'YYYY-MM-DD' or None. conditions is '' when neither is given."""
    conditions = []
    params = []
    
    if start is not None:
        conditions.append(f"{column} >= ?")
        params.append(start)
    
    if end is not None:
        end_date = date.fromisoformat(end) + timedelta(days=1)
        conditions.append(f"{column} < ?")
        params.append(end_date.isoformat())
    
    return (" AND ".join(conditions), params)


def get_machines(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, floor, has_telemetry FROM machines ORDER BY id")
    return [dict(r) | {"has_telemetry": bool(r["has_telemetry"])} for r in rows]


def get_machine(conn: sqlite3.Connection, machine_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, name, floor, has_telemetry FROM machines WHERE id = ?", (machine_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row) | {"has_telemetry": bool(row["has_telemetry"])}


def get_drink_types(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT id, name, label FROM drink_types ORDER BY id")
    return [dict(r) for r in rows]


def drink_type_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM drink_types WHERE name = ?", (name,)).fetchone()
    return row is not None


def insert_brew(
    conn: sqlite3.Connection,
    machine_id: int,
    drink_type: str,
    timestamp: str,
    duration_s: float,
    temp_c: float,
    source: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO brew_events (machine_id, drink_type, timestamp, duration_s, temp_c, source)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (machine_id, drink_type, timestamp, duration_s, temp_c, source),
    )
    return cur.lastrowid


def insert_maintenance(
    conn: sqlite3.Connection,
    machine_id: int,
    type: str,
    timestamp: str,
    note: str | None = None,
    error_code: str | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO maintenance_events (machine_id, type, timestamp, note, error_code)
        VALUES (?, ?, ?, ?, ?)
        """,
        (machine_id, type, timestamp, note, error_code),
    )
    return cur.lastrowid


def get_stats(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> dict[str, Any]:
    """Dashboard numbers: totals, per-drink, per-day."""
    
    # For total: use 'timestamp' since it's from brew_events directly
    total_cond, total_params = _range_clause("timestamp", start, end)
    
    # For per-drink: use 'be.timestamp' since it's joining with the alias
    drink_cond, drink_params = _range_clause("be.timestamp", start, end)
    
    # Total brews
    where_clause = f"WHERE {total_cond}" if total_cond else ""
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM brew_events {where_clause}",
        total_params
    ).fetchone()["n"]
    
    # Per-drink (range goes into ON, not WHERE, to preserve 0 counts)
    on_clause = f"AND {drink_cond}" if drink_cond else ""
    per_drink = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT dt.name, dt.label, COUNT(be.id) AS count
            FROM drink_types dt
            LEFT JOIN brew_events be ON be.drink_type = dt.name {on_clause}
            GROUP BY dt.id
            ORDER BY dt.id
            """,
            drink_params
        )
    ]
    
    # Per-day
    where_clause = f"WHERE {total_cond}" if total_cond else ""
    per_day = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT DATE(timestamp) AS day, COUNT(*) AS count
            FROM brew_events
            {where_clause}
            GROUP BY DATE(timestamp)
            ORDER BY day
            """,
            total_params
        )
    ]
    return {"total_brews": total, "per_drink": per_drink, "per_day": per_day}


def get_machine_health(conn: sqlite3.Connection, machine_id: int, start: str | None = None, end: str | None = None) -> dict[str, Any] | None:
    """Machine card: brew activity plus maintenance history."""
    machine = get_machine(conn, machine_id)
    if machine is None:
        return None
    
    # Use plain 'timestamp' for direct table queries, 'be.timestamp' for aliased queries
    direct_cond, direct_params = _range_clause("timestamp", start, end)
    alias_cond, alias_params = _range_clause("be.timestamp", start, end)
    
    # Brew count (filtered by range)
    where_clause = f"WHERE machine_id = ?" if not direct_cond else f"WHERE machine_id = ? AND {direct_cond}"
    brew_count_params = [machine_id] + direct_params
    brews = conn.execute(
        f"SELECT COUNT(*) AS count FROM brew_events {where_clause}",
        brew_count_params
    ).fetchone()
    
    # Last brew (all-time, unfiltered)
    last_brew = conn.execute(
        "SELECT MAX(timestamp) AS last_brew FROM brew_events WHERE machine_id = ?",
        (machine_id,)
    ).fetchone()["last_brew"]
    
    last_maintenance = conn.execute(
        """
        SELECT type, timestamp, note, error_code
        FROM maintenance_events
        WHERE machine_id = ? AND type != 'error'
        ORDER BY timestamp DESC LIMIT 1
        """,
        (machine_id,),
    ).fetchone()
    recent_errors = [
        dict(r)
        for r in conn.execute(
            """
            SELECT timestamp, error_code, note
            FROM maintenance_events
            WHERE machine_id = ? AND type = 'error'
            ORDER BY timestamp DESC LIMIT 5
            """,
            (machine_id,),
        )
    ]
    
    # Get specialty drink (most brewed, with tie detection) - filtered by range
    specialty_drink = None
    if brews["count"] > 0:
        # Get top drink with alphabetical tie-breaking
        top_drink_params = [machine_id] + alias_params
        top_drink_where = f"WHERE be.machine_id = ?" if not alias_cond else f"WHERE be.machine_id = ? AND {alias_cond}"
        top_drink = conn.execute(
            f"""
            SELECT dt.name, dt.label, COUNT(be.id) AS count
            FROM brew_events be
            JOIN drink_types dt ON be.drink_type = dt.name
            {top_drink_where}
            GROUP BY be.drink_type
            ORDER BY COUNT(be.id) DESC, dt.name ASC
            LIMIT 1
            """,
            top_drink_params
        ).fetchone()
        
        if top_drink:
            top_count = top_drink["count"]
            # Check if there's a tie (another drink with same count)
            tie_check_params = [machine_id] + alias_params + [top_count]
            tie_check_where = f"WHERE be.machine_id = ?" if not alias_cond else f"WHERE be.machine_id = ? AND {alias_cond}"
            tie_check = conn.execute(
                f"""
                SELECT COUNT(*) AS tied_count
                FROM (
                    SELECT COUNT(be.id) AS count
                    FROM brew_events be
                    {tie_check_where}
                    GROUP BY be.drink_type
                    HAVING COUNT(be.id) = ?
                )
                """,
                tie_check_params
            ).fetchone()
            is_tied = tie_check["tied_count"] > 1 if tie_check else False
            
            specialty_drink = {
                "label": top_drink["label"],
                "count": top_count,
                "is_tied": is_tied,
            }
    
    return machine | {
        "brew_count": brews["count"],
        "last_brew": last_brew,
        "specialty_drink": specialty_drink,
        "last_maintenance": dict(last_maintenance) if last_maintenance else None,
        "recent_errors": recent_errors,
    }
