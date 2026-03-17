"""SQL generation tool with template-based approach and sqlparse formatting. ANP v0.2."""

from __future__ import annotations

import re
from typing import Any

import sqlparse

from agentnode_sdk.exceptions import AgentNodeToolError


def _extract_table_columns(schema: str) -> dict[str, list[str]]:
    """Extract table names and columns from a schema string."""
    tables: dict[str, list[str]] = {}
    # Match CREATE TABLE statements
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)",
        re.IGNORECASE | re.DOTALL,
    )
    for match in table_pattern.finditer(schema):
        table_name = match.group(1)
        body = match.group(2)
        cols = []
        for line in body.split(","):
            line = line.strip()
            if not line:
                continue
            # Skip constraints
            upper = line.upper()
            if any(upper.startswith(kw) for kw in ("PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT", "INDEX")):
                continue
            parts = line.split()
            if parts:
                cols.append(parts[0].strip('"').strip("'").strip("`"))
        tables[table_name] = cols

    # Fallback: if no CREATE TABLE found, try to parse comma-separated "table(col1, col2)"
    if not tables:
        simple_pattern = re.compile(r"(\w+)\s*\(([^)]+)\)")
        for match in simple_pattern.finditer(schema):
            table_name = match.group(1).upper()
            if table_name in ("CREATE", "INSERT", "SELECT", "UPDATE", "DELETE", "TABLE", "INTO", "FROM"):
                continue
            cols = [c.strip().split()[0] for c in match.group(2).split(",")]
            tables[match.group(1)] = cols

    return tables


def _detect_intent(description: str) -> str:
    """Detect SQL intent from natural language description."""
    desc_lower = description.lower()

    if any(kw in desc_lower for kw in ("create table", "new table", "define table", "create a table")):
        return "CREATE"
    if any(kw in desc_lower for kw in ("insert", "add record", "add row", "add a new", "add data")):
        return "INSERT"
    if any(kw in desc_lower for kw in ("update", "modify", "change", "set the", "set value")):
        return "UPDATE"
    if any(kw in desc_lower for kw in ("delete", "remove", "drop row")):
        return "DELETE"
    # Default to SELECT
    return "SELECT"


def _extract_mentioned_words(description: str) -> list[str]:
    """Extract significant words from the description."""
    stop_words = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "shall", "can", "need", "dare", "ought",
        "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "above",
        "below", "between", "out", "off", "over", "under", "again",
        "further", "then", "once", "all", "each", "every", "both", "few",
        "more", "most", "other", "some", "such", "no", "not", "only",
        "own", "same", "so", "than", "too", "very", "just", "because",
        "but", "and", "or", "if", "while", "that", "this", "these",
        "those", "i", "me", "my", "we", "our", "you", "your", "it",
        "its", "they", "them", "their", "what", "which", "who", "whom",
        "where", "when", "how", "get", "find", "show", "list", "give",
        "select", "insert", "update", "delete", "create", "table", "query",
        "sql", "generate", "write", "make",
    }
    words = re.findall(r"\w+", description.lower())
    return [w for w in words if w not in stop_words and len(w) > 1]


def _find_matching_tables(
    words: list[str], tables: dict[str, list[str]]
) -> list[str]:
    """Find tables whose names match any of the words."""
    matched = []
    for table in tables:
        tl = table.lower()
        for word in words:
            if word in tl or tl in word:
                matched.append(table)
                break
    return matched if matched else list(tables.keys())[:1]


def _build_select(
    description: str, tables: dict[str, list[str]], words: list[str], dialect: str,
) -> str:
    """Build a SELECT query."""
    desc_lower = description.lower()
    target_tables = _find_matching_tables(words, tables)

    # Determine columns
    use_star = True
    selected_cols: list[str] = []
    if target_tables:
        main_table = target_tables[0]
        available_cols = tables.get(main_table, [])
        for col in available_cols:
            if col.lower() in words:
                selected_cols.append(col)
                use_star = False

    cols_str = "*" if use_star else ", ".join(selected_cols)
    main_table = target_tables[0] if target_tables else "table_name"

    query = f"SELECT {cols_str}\nFROM {main_table}"

    # JOIN detection
    if len(target_tables) > 1 and any(kw in desc_lower for kw in ("join", "combine", "with", "related", "and their")):
        for join_table in target_tables[1:]:
            join_cols = tables.get(join_table, [])
            main_cols = tables.get(main_table, [])
            # Try to find a matching FK column
            fk_col = None
            for mc in main_cols:
                if join_table.lower().rstrip("s") in mc.lower():
                    fk_col = mc
                    break
            if not fk_col:
                fk_col = f"{join_table.rstrip('s')}_id"
            pk_col = "id"
            if join_cols and join_cols[0].lower() in ("id", join_cols[0]):
                pk_col = join_cols[0]
            query += f"\nJOIN {join_table} ON {main_table}.{fk_col} = {join_table}.{pk_col}"

    # WHERE clause
    where_patterns = [
        (r"where\s+(\w+)\s*=\s*['\"]?(\w+)['\"]?", "="),
        (r"(\w+)\s+(?:equals?|is)\s+['\"]?(\w+)['\"]?", "="),
        (r"(\w+)\s+(?:greater|more|above|over)\s+(?:than\s+)?(\d+)", ">"),
        (r"(\w+)\s+(?:less|fewer|below|under)\s+(?:than\s+)?(\d+)", "<"),
    ]
    for pat, op in where_patterns:
        m = re.search(pat, desc_lower)
        if m:
            col_name, val = m.group(1), m.group(2)
            try:
                val_formatted = str(int(val))
            except ValueError:
                val_formatted = f"'{val}'"
            query += f"\nWHERE {col_name} {op} {val_formatted}"
            break

    # ORDER BY
    order_match = re.search(r"(?:order|sort)\s+by\s+(\w+)(?:\s+(asc|desc))?", desc_lower)
    if order_match:
        order_col = order_match.group(1)
        direction = (order_match.group(2) or "ASC").upper()
        query += f"\nORDER BY {order_col} {direction}"

    # GROUP BY
    if any(kw in desc_lower for kw in ("group by", "count", "sum", "average", "avg", "total")):
        group_match = re.search(r"group\s+by\s+(\w+)", desc_lower)
        if group_match:
            query += f"\nGROUP BY {group_match.group(1)}"
        if "count" in desc_lower:
            query = query.replace("SELECT *", "SELECT COUNT(*)")

    # LIMIT
    limit_match = re.search(r"(?:limit|top|first)\s+(\d+)", desc_lower)
    if limit_match:
        n = limit_match.group(1)
        if dialect.lower() == "mssql":
            query = query.replace("SELECT", f"SELECT TOP {n}", 1)
        else:
            query += f"\nLIMIT {n}"

    return query


def _build_insert(
    description: str, tables: dict[str, list[str]], words: list[str], dialect: str,
) -> str:
    """Build an INSERT query."""
    target_tables = _find_matching_tables(words, tables)
    main_table = target_tables[0] if target_tables else "table_name"
    cols = tables.get(main_table, ["column1", "column2"])

    cols_str = ", ".join(cols)
    placeholders = ", ".join(["%s" if dialect.lower() == "mysql" else "$" + str(i + 1) if dialect.lower() == "postgresql" else "?" for i in range(len(cols))])

    return f"INSERT INTO {main_table} ({cols_str})\nVALUES ({placeholders})"


def _build_update(
    description: str, tables: dict[str, list[str]], words: list[str], dialect: str,
) -> str:
    """Build an UPDATE query."""
    target_tables = _find_matching_tables(words, tables)
    main_table = target_tables[0] if target_tables else "table_name"
    cols = tables.get(main_table, [])

    # Find mentioned columns
    set_cols = [c for c in cols if c.lower() in words] if cols else []
    if not set_cols and cols:
        set_cols = cols[1:2]  # Skip id, take first data column

    set_clauses = ", ".join([f"{c} = %s" for c in set_cols]) if set_cols else "column_name = %s"
    pk = cols[0] if cols else "id"

    return f"UPDATE {main_table}\nSET {set_clauses}\nWHERE {pk} = %s"


def _build_delete(
    description: str, tables: dict[str, list[str]], words: list[str], dialect: str,
) -> str:
    """Build a DELETE query."""
    target_tables = _find_matching_tables(words, tables)
    main_table = target_tables[0] if target_tables else "table_name"
    cols = tables.get(main_table, [])
    pk = cols[0] if cols else "id"

    return f"DELETE FROM {main_table}\nWHERE {pk} = %s"


def _build_create_table(
    description: str, tables: dict[str, list[str]], words: list[str], dialect: str,
) -> str:
    """Build a CREATE TABLE query."""
    # Try to extract the table name from the description
    name_match = re.search(r"(?:table|called|named)\s+['\"]?(\w+)['\"]?", description.lower())
    table_name = name_match.group(1) if name_match else "new_table"

    # Extract column hints from description
    col_hints = re.findall(r"(\w+)\s+(?:as\s+)?(\w+(?:\s*\(\d+\))?)", description.lower())

    pk_type = "SERIAL" if dialect.lower() == "postgresql" else "INTEGER PRIMARY KEY AUTOINCREMENT" if dialect.lower() == "sqlite" else "INT AUTO_INCREMENT"

    columns = [f"    id {pk_type} PRIMARY KEY" if dialect.lower() == "postgresql" else f"    id {pk_type}"]

    if col_hints:
        for col_name, col_type in col_hints:
            if col_name.lower() in ("table", "called", "named", "create", "column", "with", "and"):
                continue
            columns.append(f"    {col_name} {col_type.upper()}")
    else:
        columns.extend([
            "    name VARCHAR(255) NOT NULL",
            "    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        ])

    cols_str = ",\n".join(columns)
    return f"CREATE TABLE {table_name} (\n{cols_str}\n)"


def generate(description: str, schema: str = "", dialect: str = "postgresql") -> dict:
    """Generate SQL from a natural language description.

    Args:
        description: Natural language description of what the SQL should do.
        schema: Optional database schema (CREATE TABLE statements or table(col1,col2) notation).
        dialect: SQL dialect - "postgresql", "mysql", "sqlite", "mssql".

    Returns:
        A dict with the generated SQL and metadata.
    """
    if not description or not description.strip():
        raise AgentNodeToolError("description is required", tool_name="generate_sql")

    tables = _extract_table_columns(schema) if schema else {}
    words = _extract_mentioned_words(description)
    intent = _detect_intent(description)

    builders = {
        "SELECT": _build_select,
        "INSERT": _build_insert,
        "UPDATE": _build_update,
        "DELETE": _build_delete,
        "CREATE": _build_create_table,
    }

    builder = builders.get(intent, _build_select)
    raw_sql = builder(description, tables, words, dialect)

    # Format with sqlparse
    formatted_sql = sqlparse.format(
        raw_sql,
        reindent=True,
        keyword_case="upper",
        identifier_case=None,
        strip_comments=False,
    )

    # Parse for validation
    parsed = sqlparse.parse(formatted_sql)
    statement_types = [str(stmt.get_type()) for stmt in parsed if stmt.get_type()]

    return {
        "sql": formatted_sql,
        "intent": intent,
        "dialect": dialect,
        "tables_referenced": list(tables.keys()) if tables else [],
        "statement_types": statement_types,
        "description": description,
    }


def format_sql(sql: str, dialect: str = "postgresql") -> dict:
    """Format and validate an existing SQL query.

    Args:
        sql: Raw SQL query to format.
        dialect: SQL dialect for formatting hints.

    Returns:
        A dict with the formatted SQL and parse info.
    """
    if not sql or not sql.strip():
        raise AgentNodeToolError("sql is required", tool_name="format_sql")

    formatted = sqlparse.format(
        sql,
        reindent=True,
        keyword_case="upper",
        identifier_case=None,
        strip_comments=False,
    )

    parsed = sqlparse.parse(formatted)
    statement_types = [str(stmt.get_type()) for stmt in parsed if stmt.get_type()]

    return {
        "sql": formatted,
        "original": sql,
        "dialect": dialect,
        "statement_types": statement_types,
        "statement_count": len(parsed),
    }


# Backward-compatible v0.1 entrypoint
def run(
    description: str,
    schema: str = "",
    dialect: str = "postgresql",
) -> dict:
    """Generate SQL from a natural language description (v0.1 compatibility wrapper)."""
    return generate(description, schema=schema, dialect=dialect)
