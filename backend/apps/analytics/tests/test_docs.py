"""Every query in sql/ is documented in docs/analytics.md, where its comment says.

Each .sql file starts by pointing to its section (docs/analytics.md#anchor). Without
this test, renaming a section or adding a query without documenting it would leave
the comment pointing nowhere.
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

from apps.analytics.queries import SQL_DIR

ANALYTICS = Path(settings.BASE_DIR).parent / "docs" / "analytics.md"
LINK = re.compile(r"docs/analytics\.md#([a-z0-9-]+)")
QUERIES = sorted(SQL_DIR.glob("*.sql"))


def anchors(markdown: str) -> set[str]:
    """The anchors GitHub gives the headings: lowercase, punctuation dropped,
    spaces turned into hyphens."""
    headings = re.findall(r"^#{1,6} (.+)$", markdown, flags=re.MULTILINE)
    return {re.sub(r"[^a-z0-9 -]", "", h.lower()).replace(" ", "-") for h in headings}


def test_there_are_queries_to_check():
    assert len(QUERIES) >= 6


@pytest.mark.parametrize("query", QUERIES, ids=lambda path: path.stem)
def test_each_query_points_to_its_section(query):
    head = "\n".join(query.read_text(encoding="utf-8").splitlines()[:3])
    links = LINK.findall(head)
    documented = anchors(ANALYTICS.read_text(encoding="utf-8"))

    assert links, f"{query.name} does not say where it is documented"
    assert set(links) <= documented, links
    assert f"`{query.name}`" in ANALYTICS.read_text(encoding="utf-8")
