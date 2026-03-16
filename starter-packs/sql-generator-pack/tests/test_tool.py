"""Tests for sql-generator-pack."""


def test_run_select_query():
    from sql_generator_pack.tool import run

    result = run(
        description="select all users where status is active",
        schema="users(id, name, status)",
        dialect="postgresql",
    )
    assert "sql" in result
    assert result["dialect"] == "postgresql"
    assert "SELECT" in result["sql"].upper()
    assert "users" in result["sql"].lower()


def test_run_insert_query():
    from sql_generator_pack.tool import run

    result = run(description="insert a new user with name Alice", schema="users(id, name)")
    assert "sql" in result
    assert "INSERT" in result["sql"].upper()


def test_run_no_schema():
    from sql_generator_pack.tool import run

    result = run(description="select all from orders")
    assert "sql" in result
