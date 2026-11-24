"""Time the dashboard queries and the client ticket list on helpdesk_bench.

Each query runs as the application role, with the row-level security context of a
real request, under EXPLAIN ANALYZE; the best of --runs times is printed. Every
statement runs in one transaction that is rolled back, so --setup can try an
index or a setting without changing the database:

    make bench-queries
    make bench-queries ARGS="--setup candidate.sql"   # e.g. CREATE INDEX ...;

Clients: the largest (11 % of the tickets in the volume seed) and the smallest
(0.8 %). Numbers vary from run to run by about 5 %; compare runs made one after
the other. See docs/analytics.md#performance.
"""

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "backend" / "apps" / "analytics" / "sql"
QUERIES = [
    "sla_compliance",
    "response_times",
    "agent_workload",
    "category_trends",
    "daily_backlog",
]
LARGEST, SMALLEST = 4, 43
# One year up to 24 November 2026, as the default dashboard period: midnights in
# Madrid, end exclusive.
PARAMS = {
    "start": "'2025-11-30 23:00+00'",
    "end": "'2026-11-24 23:00+00'",
    "tz": "'Europe/Madrid'",
    "now": "now()",
    "priorities": "ARRAY['low', 'medium', 'high', 'critical']",
}


def render(name: str, organization: str) -> str:
    """The query with its parameters written in, as psql needs it."""
    sql = (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8").replace("%%", "%")
    for key, value in {**PARAMS, "org": organization}.items():
        sql = sql.replace(f"%({key})s", value)
    return sql


def cases():
    """(label, app.role, app.org_id, query)."""
    for name in QUERIES:
        yield f"{name} all", "agent", "", render(name, "NULL")
        for org in (LARGEST, SMALLEST):
            yield f"{name} org {org}", "agent", "", render(name, str(org))
    list_query = "SELECT * FROM ticket WHERE organization_id = {org}{extra} "
    list_query += "ORDER BY created_at DESC, id DESC LIMIT 26"
    for org in (LARGEST, SMALLEST):
        yield f"ticket list org {org}", "client", str(org), list_query.format(org=org, extra="")
        deep = list_query.format(org=org, extra=" AND created_at < '2025-03-01'")
        yield f"ticket list org {org}, deep", "client", str(org), deep


def script(setup: str, runs: int) -> tuple[list[str], str]:
    labels, lines = [], ["BEGIN;", setup]
    for label, role, org, query in cases():
        labels.append(label)
        lines += [
            "SET LOCAL ROLE helpdesk_app;",
            f"SELECT set_config('app.role', '{role}', true),"
            f" set_config('app.org_id', '{org}', true);",
            *[f"EXPLAIN (ANALYZE, COSTS OFF) {query};" for _ in range(runs)],
            "RESET ROLE;",
        ]
    lines.append("ROLLBACK;")
    return labels, "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--setup", type=Path, help="SQL run first, inside the transaction")
    parser.add_argument("--runs", type=int, default=3, help="runs per query (default 3)")
    args = parser.parse_args()

    setup = args.setup.read_text(encoding="utf-8") if args.setup else ""
    labels, sql = script(setup, args.runs)
    psql = ["docker", "compose", "exec", "-T", "db", "psql", "-q", "--dbname=helpdesk_bench"]
    result = subprocess.run(
        [*psql, "--set=ON_ERROR_STOP=1"],
        input=sql,
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        raise SystemExit(result.stderr)
    times = [float(t) for t in re.findall(r"Execution Time: ([\d.]+) ms", result.stdout)]
    for i, label in enumerate(labels):
        best = min(times[i * args.runs : (i + 1) * args.runs])
        print(f"{label:30} {best:8.1f} ms")


if __name__ == "__main__":
    main()
