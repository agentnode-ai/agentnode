"""
Database query tool using BaseTool with instance state.
Uses self.connection_string and self.db_name in _run body — should break on import.
"""

from typing import List, Optional, Type

from langchain.tools import BaseTool
from pydantic import BaseModel, Field


class DatabaseQueryInput(BaseModel):
    query: str = Field(..., description="SQL query to execute")
    limit: int = Field(default=100, description="Max rows to return")


class DatabaseQueryTool(BaseTool):
    name: str = "database_query"
    description: str = "Execute a SQL query against the configured database and return results."
    args_schema: Type[BaseModel] = DatabaseQueryInput

    connection_string: str = Field(default="", description="Database connection string")
    db_name: str = Field(default="", description="Database name")
    timeout: int = Field(default=30, description="Query timeout in seconds")

    def _run(self, query: str, limit: int = 100) -> dict:
        """Execute query against self.connection_string."""
        import sqlalchemy

        # uses self references throughout body
        engine = sqlalchemy.create_engine(
            self.connection_string,
            connect_args={"options": f"-csearch_path={self.db_name}"},
        )
        try:
            with engine.connect() as conn:
                result = conn.execute(
                    sqlalchemy.text(query + f" LIMIT {limit}"),
                    execution_options={"timeout": self.timeout},
                )
                rows = [dict(row._mapping) for row in result]
                return {
                    "query": query,
                    "rows": rows,
                    "row_count": len(rows),
                    "db": self.db_name,
                    "error": None,
                }
        except Exception as e:
            return {
                "query": query,
                "rows": [],
                "row_count": 0,
                "db": self.db_name,
                "error": str(e),
            }
        finally:
            engine.dispose()

    async def _arun(self, query: str, limit: int = 100) -> dict:
        raise NotImplementedError
