"""Tests for database-connector-pack."""

import os
import tempfile


def test_run_sqlite_create_and_query():
    from database_connector_pack.tool import run

    # Use a fixed temp file path to avoid Windows lock issues with TemporaryDirectory
    db_path = os.path.join(tempfile.gettempdir(), "agentnode_test_db.db")
    conn = f"sqlite:///{db_path}"

    try:
        run(conn, operation="execute", sql="CREATE TABLE IF NOT EXISTS test_users (id INTEGER PRIMARY KEY, name TEXT)")
        run(conn, operation="execute", sql="INSERT INTO test_users (name) VALUES ('Alice')")

        result = run(conn, operation="tables")
        assert "tables" in result
        assert "test_users" in result["tables"]

        result2 = run(conn, operation="query", sql="SELECT * FROM test_users")
        assert "rows" in result2 or "results" in result2 or "data" in result2
    finally:
        try:
            os.unlink(db_path)
        except OSError:
            pass
