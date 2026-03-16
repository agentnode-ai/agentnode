"""Database connector using SQLAlchemy for queries and schema introspection."""

from __future__ import annotations


def run(connection_string: str, operation: str, **kwargs) -> dict:
    """Interact with a database via SQLAlchemy.

    Args:
        connection_string: SQLAlchemy connection string
            (e.g. "sqlite:///mydb.db", "postgresql://user:pass@host/db").
        operation: One of "query", "tables", "schema", "execute".
        **kwargs:
            sql (str): SQL statement (for "query" and "execute").
            table_name (str): Table name (for "schema").

    Returns:
        For "query": {"rows": list, "columns": list, "row_count": int}
        For "tables": {"tables": list}
        For "schema": {"table": str, "columns": list}
        For "execute": {"row_count": int, "status": str}
    """
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(connection_string)

    ops = {
        "query": _query,
        "tables": _tables,
        "schema": _schema,
        "execute": _execute,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    return ops[operation](engine, **kwargs)


def _query(engine, **kwargs) -> dict:
    from sqlalchemy import text

    sql = kwargs.get("sql", "")
    if not sql:
        raise ValueError("sql is required for query")

    with engine.connect() as conn:
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]

    return {
        "rows": rows,
        "columns": columns,
        "row_count": len(rows),
    }


def _tables(engine, **kwargs) -> dict:
    from sqlalchemy import inspect as sa_inspect

    inspector = sa_inspect(engine)
    table_names = inspector.get_table_names()
    return {"tables": table_names, "total": len(table_names)}


def _schema(engine, **kwargs) -> dict:
    from sqlalchemy import inspect as sa_inspect

    table_name = kwargs.get("table_name", "")
    if not table_name:
        raise ValueError("table_name is required for schema")

    inspector = sa_inspect(engine)
    columns = inspector.get_columns(table_name)

    col_info = []
    for col in columns:
        col_info.append({
            "name": col.get("name", ""),
            "type": str(col.get("type", "")),
            "nullable": col.get("nullable", True),
            "default": str(col.get("default")) if col.get("default") is not None else None,
            "primary_key": col.get("autoincrement", False) is True,
        })

    # Get primary key info
    pk = inspector.get_pk_constraint(table_name)
    pk_columns = pk.get("constrained_columns", []) if pk else []

    # Mark actual primary key columns
    for c in col_info:
        c["primary_key"] = c["name"] in pk_columns

    # Get foreign keys
    fks = inspector.get_foreign_keys(table_name)
    foreign_keys = []
    for fk in fks:
        foreign_keys.append({
            "columns": fk.get("constrained_columns", []),
            "referred_table": fk.get("referred_table", ""),
            "referred_columns": fk.get("referred_columns", []),
        })

    return {
        "table": table_name,
        "columns": col_info,
        "primary_keys": pk_columns,
        "foreign_keys": foreign_keys,
    }


def _execute(engine, **kwargs) -> dict:
    from sqlalchemy import text

    sql = kwargs.get("sql", "")
    if not sql:
        raise ValueError("sql is required for execute")

    with engine.connect() as conn:
        result = conn.execute(text(sql))
        conn.commit()
        row_count = result.rowcount

    return {
        "row_count": row_count,
        "status": "success",
    }
