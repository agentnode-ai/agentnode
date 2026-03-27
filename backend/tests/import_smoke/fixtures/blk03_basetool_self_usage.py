from langchain.tools import BaseTool


class DatabaseQuery(BaseTool):
    name = "database_query"
    description = "Run a SQL query against the configured database"
    connection_string: str = ""

    def _run(self, query: str) -> dict:
        import psycopg2
        conn = psycopg2.connect(self.connection_string)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        return {"columns": columns, "rows": rows, "count": len(rows)}
