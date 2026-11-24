"""The documented demo accounts and SLA figures follow catalog.py.

Without this, renaming an account or changing a policy in the catalog would leave
the README and docs/demo-data.md telling people to log in with an account that no
longer exists.
"""

from pathlib import Path

from django.conf import settings

from apps.accounts.models import Role
from apps.demo.catalog import CLIENT_POLICIES, CLIENTS, DEFAULT_POLICIES, STAFF, STAFF_DOMAIN

ROOT = Path(settings.BASE_DIR).parent
README = ROOT / "README.md"
DEMO_DATA = ROOT / "docs" / "demo-data.md"


def demo_accounts():
    """The admin, the first agent and the first user of each demo client."""
    admin = next(local for local, _, role, _ in STAFF if role == Role.ADMIN)
    agent = next(local for local, _, role, _ in STAFF if role == Role.AGENT)
    return [
        f"{admin}@{STAFF_DOMAIN}",
        f"{agent}@{STAFF_DOMAIN}",
        *(f"{people[0][0]}@{slug}.example" for slug, people in CLIENTS.items()),
    ]


def test_demo_accounts_are_documented():
    for document in (README, DEMO_DATA):
        text = document.read_text(encoding="utf-8")
        missing = [account for account in demo_accounts() if f"`{account}`" not in text]
        assert missing == [], document.name


def test_sla_figures_in_the_document_match_the_catalog():
    text = DEMO_DATA.read_text(encoding="utf-8")
    policies = [*DEFAULT_POLICIES.items(), *CLIENT_POLICIES["vallebueno"].items()]

    for priority, (first, resolution) in policies:
        assert f"{priority} {first}/{resolution}" in text, priority
