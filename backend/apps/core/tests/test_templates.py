"""Checks over every template in the project, rendered by a test or not."""

import re
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[2]
TEMPLATES = sorted(
    path for path in APPS.glob("*/templates/**/*") if path.suffix in {".html", ".txt"}
)
# A {# opened but not closed on the same line.
UNCLOSED_COMMENT = re.compile(r"\{#(?!.*#\})")


def test_the_templates_are_found():
    # Without this, a wrong glob would make the test below check nothing.
    assert any(path.name == "base.html" for path in TEMPLATES)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_one_line_comments_end_on_their_line(template):
    # Django's {# #} only works on a single line; a multi-line one is printed as
    # text. Longer comments use {% comment %} ... {% endcomment %}.
    for number, line in enumerate(template.read_text(encoding="utf-8").splitlines(), 1):
        assert not UNCLOSED_COMMENT.search(line), f"line {number}: {line.strip()}"
