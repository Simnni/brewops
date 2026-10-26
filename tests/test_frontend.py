"""Frontend-adjacent checks: the app serves its own static files."""

import pytest

from brewops.db.connection import connect
from brewops.db.schema import init_db

from asgi_client import request


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "static-test.db"
    monkeypatch.setenv("BREWOPS_DB", str(path))
    conn = connect(path)
    init_db(conn)
    conn.commit()
    yield conn
    conn.close()


def get_app():
    # imported lazily so the static mount reflects the frontend directory
    from brewops.api.main import app
    return app


def test_index_served(db):
    r = request(get_app(), "GET", "/")
    assert r.status == 200
    assert "text/html" in r.headers.get("content-type", "")
    assert "Log a brew" in r.text
    assert "Log maintenance" in r.text


def test_static_assets_served(db):
    js = request(get_app(), "GET", "/app.js")
    assert js.status == 200
    assert "loadDashboard" in js.text
    css = request(get_app(), "GET", "/style.css")
    assert css.status == 200
    assert "bar-fill" in css.text


def test_frontend_has_no_external_resources():
    # the SVG namespace is an identifier, not a fetched resource
    allowed = ("http://www.w3.org/2000/svg", "http://localhost")
    from brewops.api.main import FRONTEND_DIR
    for f in FRONTEND_DIR.iterdir():
        content = f.read_text(encoding="utf-8").lower()
        for a in allowed:
            content = content.replace(a, "")
        assert "http://" not in content, f.name
        assert "https://" not in content, f.name


def test_frontend_has_date_filter_elements(db):
    r = request(get_app(), "GET", "/")
    assert r.status == 200
    html = r.text
    # Check for filter input elements
    assert 'id="filter-start"' in html
    assert 'id="filter-end"' in html
    # Check for preset buttons
    assert 'id="filter-7d"' in html
    assert 'id="filter-30d"' in html
    assert 'id="filter-all"' in html


def test_frontend_app_has_filter_query_function(db):
    r = request(get_app(), "GET", "/app.js")
    assert r.status == 200
    js = r.text
    assert "filterQuery" in js
    assert "/api/stats" in js

