"""Run the dashboard queries kept as .sql files in sql/.

Each query is a plain SQL file, readable and runnable in psql as it is (with the
parameters filled in). Parameters are named, psycopg style: %(start)s. A literal
percent sign must be written %%, in comments too: psycopg formats the whole text.

The queries run on the request's connection, inside its transaction and with its
row-level security context, like any ORM query: a client's query only sees that
client's rows even if the SQL forgot to filter (the application filters as well).
"""

import re
from functools import cache
from pathlib import Path

from django.db import connection

SQL_DIR = Path(__file__).resolve().parent / "sql"
# A bare file name: no directories, no extension.
NAME = re.compile(r"[a-z][a-z0-9_]*")


@cache
def load(name: str) -> str:
    """The text of sql/<name>.sql, read once per process."""
    if not NAME.fullmatch(name):
        raise ValueError(f"Invalid query name {name!r}.")
    return (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8")


def run(name: str, params: dict) -> list[dict]:
    """Rows of the query as dicts keyed by column name, in the query's order."""
    with connection.cursor() as cursor:
        cursor.execute(load(name), params)
        columns = [column.name for column in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
