import pytest

from brewops.db.connection import connect
from brewops.db.queries import (
    get_drink_types,
    get_machine_health,
    get_machines,
    get_stats,
    insert_brew,
    insert_maintenance,
)
from brewops.db.schema import init_db, reset_db


@pytest.fixture
def conn(tmp_path):
    conn = connect(tmp_path / "test.db")
    init_db(conn)
    yield conn
    conn.close()


def test_init_is_idempotent(conn):
    init_db(conn)
    init_db(conn)
    assert len(get_machines(conn)) == 4
    assert len(get_drink_types(conn)) == 6


def test_reference_data_seeded(conn):
    machines = {m["name"]: m for m in get_machines(conn)}
    assert "Bertha (3rd floor)" in machines
    assert machines["Old Faithful (2nd floor)"]["has_telemetry"] is False
    drinks = {d["name"] for d in get_drink_types(conn)}
    assert "espresso" in drinks


def test_stats_math(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 3, "lungo", "2026-06-02 11:00:00", 38.0, 90.0, "manual")
    conn.commit()

    stats = get_stats(conn)
    assert stats["total_brews"] == 4
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 2
    assert per_drink["latte"] == 1
    assert per_drink["cappuccino"] == 0
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-01": 2, "2026-06-02": 2}


def test_machine_health(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    health = get_machine_health(conn, 4)
    assert health["brew_count"] == 1
    assert health["last_brew"] == "2026-06-01 08:00:00"
    assert health["last_maintenance"]["type"] == "descale"
    assert health["recent_errors"][0]["error_code"] == "E42"


def test_machine_health_unknown_machine(conn):
    assert get_machine_health(conn, 999) is None


def test_reset_db_clears_events(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    conn.commit()
    reset_db(conn)
    assert get_stats(conn)["total_brews"] == 0
    assert len(get_machines(conn)) == 4


def test_stats_with_date_range(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.5, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-01 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 3, "lungo", "2026-06-02 11:00:00", 38.0, 90.0, "manual")
    conn.commit()

    # Single day range
    stats = get_stats(conn, "2026-06-02", "2026-06-02")
    assert stats["total_brews"] == 2
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert per_drink["espresso"] == 0
    assert per_drink["latte"] == 1
    assert per_drink["lungo"] == 1
    assert per_drink["cappuccino"] == 0  # Still has zero-count drinks
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day == {"2026-06-02": 2}


def test_stats_end_day_inclusive(conn):
    insert_brew(conn, 1, "espresso", "2026-06-02 23:59:59", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-03 00:00:00", 26.0, 91.5, "csv")
    conn.commit()

    # 2026-06-02 should include the 23:59:59 brew but not the 00:00:00 one
    stats = get_stats(conn, "2026-06-02", "2026-06-02")
    assert stats["total_brews"] == 1
    assert stats["per_day"] == [{"day": "2026-06-02", "count": 1}]


def test_stats_start_only(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-02 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-03 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, "2026-06-02", None)
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert "2026-06-01" not in per_day
    assert per_day["2026-06-02"] == 1
    assert per_day["2026-06-03"] == 1


def test_stats_end_only(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 1, "espresso", "2026-06-02 09:00:00", 26.0, 91.5, "csv")
    insert_brew(conn, 2, "latte", "2026-06-03 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    stats = get_stats(conn, None, "2026-06-02")
    assert stats["total_brews"] == 2
    per_day = {d["day"]: d["count"] for d in stats["per_day"]}
    assert per_day["2026-06-01"] == 1
    assert per_day["2026-06-02"] == 1
    assert "2026-06-03" not in per_day


def test_stats_empty_range_preserves_drink_list(conn):
    insert_brew(conn, 1, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    conn.commit()

    # Query a range with no brews
    stats = get_stats(conn, "2026-07-01", "2026-07-31")
    assert stats["total_brews"] == 0
    assert stats["per_day"] == []
    # All drinks should still be in per_drink with count 0
    assert len(stats["per_drink"]) == 6
    per_drink = {d["name"]: d["count"] for d in stats["per_drink"]}
    assert all(count == 0 for count in per_drink.values())


def test_machine_health_with_date_range(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    insert_maintenance(conn, 4, "error", "2026-06-04 09:15:00", error_code="E42")
    conn.commit()

    # Query only 2026-06-02
    health = get_machine_health(conn, 4, "2026-06-02", "2026-06-02")
    assert health["brew_count"] == 1  # Only latte on 2026-06-02
    assert health["last_brew"] == "2026-06-02 10:00:00"  # All-time last brew
    assert health["last_maintenance"]["type"] == "descale"  # Unfiltered
    assert health["recent_errors"][0]["error_code"] == "E42"  # Unfiltered


def test_machine_health_empty_range(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_maintenance(conn, 4, "descale", "2026-06-03 18:00:00", note="quarterly descale")
    conn.commit()

    # Query a range with no brews for this machine
    health = get_machine_health(conn, 4, "2026-07-01", "2026-07-31")
    assert health["brew_count"] == 0
    assert health["specialty_drink"] is None
    assert health["last_maintenance"]["type"] == "descale"  # Still there (unfiltered)


def test_machine_health_tie_detection_in_range(conn):
    insert_brew(conn, 4, "espresso", "2026-06-01 08:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "espresso", "2026-06-01 09:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "espresso", "2026-06-01 10:00:00", 27.0, 92.0, "csv")
    insert_brew(conn, 4, "latte", "2026-06-01 11:00:00", 44.0, 88.0, "csv")
    insert_brew(conn, 4, "latte", "2026-06-02 10:00:00", 44.0, 88.0, "csv")
    conn.commit()

    # All-time: espresso wins 3-1
    health = get_machine_health(conn, 4)
    assert health["specialty_drink"]["label"] == "Espresso"
    assert not health["specialty_drink"]["is_tied"]

    # In 2026-06-02: only latte, so no tie
    health = get_machine_health(conn, 4, "2026-06-02", "2026-06-02")
    assert health["brew_count"] == 1
    assert health["specialty_drink"]["label"] == "Latte"
    assert not health["specialty_drink"]["is_tied"]

    # In 2026-06-01-06-02: espresso 3, latte 2, no tie
    health = get_machine_health(conn, 4, "2026-06-01", "2026-06-02")
    assert health["brew_count"] == 5
    assert health["specialty_drink"]["label"] == "Espresso"
    assert not health["specialty_drink"]["is_tied"]
