from langchain.tools import BaseTool


class DatabaseQueryTool(BaseTool):
    name = "db_query"
    description = "Execute a database query"
    connection_string: str = ""

    def _run(self, query: str) -> dict:
        import psycopg2
        conn = psycopg2.connect(self.connection_string)
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return {"rows": rows, "count": len(rows)}
