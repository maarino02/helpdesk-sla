"""The demo history as a plan: what happens to each ticket, when and by whom.

No database access: given the same catalog, seed and `now`, the plan is the same,
so it can be tested on its own. replay.py then runs it through the real services,
which compute deadlines, pauses, business minutes and events exactly as in
production.

Instants are chosen in business time where a person on the provider's side acts
(first response, work) and in wall-clock time where a client reacts (replies,
confirmations). Steps later than `now` are cut: those tickets stay open, pending
or resolved, as a live helpdesk would have them.
"""

import datetime as dt
import random
from dataclasses import dataclass, field
from itertools import takewhile

from apps.demo import texts
from apps.sla.business_time import add_business_minutes, business_minutes_between
from apps.sla.models import Priority
from apps.tickets.models import Status

HISTORY = dt.timedelta(days=182)
# People and categories that are inactive in the catalog were active until this
# long before `now`: older tickets may use them, newer ones cannot.
RETIRED_BEFORE_NOW = dt.timedelta(days=90)
# Margin so that their tickets are finished (mostly) before they leave.
LAST_TICKET_BEFORE_RETIRING = dt.timedelta(days=30)
AUTO_CLOSE_AFTER = dt.timedelta(days=7)  # the auto-close job's setting

# Share of the tickets per client slug, and of the priorities.
ORGANIZATION_SHARE = {"alameda": 0.45, "pacifico": 0.35, "vallebueno": 0.20}
PRIORITY_WEIGHTS = {Priority.CRITICAL: 5, Priority.HIGH: 20, Priority.MEDIUM: 50, Priority.LOW: 25}
PRIORITY_ORDER = [Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.CRITICAL]

# Monday is the busiest day. Calendars without weekend hours get few weekend tickets.
WEEKDAY_WEIGHTS = (1.3, 1.1, 1.0, 1.0, 0.8, 0.1, 0.1)
ALWAYS_OPEN_WEEKEND_WEIGHT = 0.6
# Local hour of creation: mornings peak, some after-hours tickets.
OFFICE_HOUR_WEIGHTS = (
    *[0.1] * 7,  # 00-06
    0.5, 1.5, 3, 3, 3, 3, 2, 1.5, 2.5, 2.5, 2.5, 1,  # 07-18
    0.4, 0.4, 0.4, 0.2, 0.2,  # 19-23
)  # fmt: skip
ALWAYS_OPEN_HOUR_WEIGHTS = tuple(max(weight, 0.6) for weight in OFFICE_HOUR_WEIGHTS)
# Recent months are busier: weight grows linearly up to this factor at `now`.
GROWTH = 1.4

# Lateness: share of tickets past the target, and how far past it they go.
FIRST_RESPONSE_LATE = (0.09, 3.0)
RESOLUTION_LATE = (0.08, 2.5)

CHANCE = {
    "raise_priority_or_lower": 0.10,
    "reassign": 0.05,
    "internal_note": 0.30,
    "ask_for_information": 0.25,
    "client_answers": 0.85,
    "reopened": 0.08,
    "client_closes": 0.55,
    "thanks": 0.5,
    "admin_takes_it": 0.05,
}

COMMENT, TRANSITION, UPDATE = "comment", "transition", "update"


@dataclass(frozen=True)
class Step:
    at: dt.datetime
    kind: str
    actor: object  # a User, or None for the system
    body: str = ""
    internal: bool = False
    status: str = ""
    changes: dict = field(default_factory=dict)  # update_ticket keyword arguments


@dataclass
class TicketPlan:
    organization: object
    created_at: dt.datetime
    requester: object
    category: object
    priority: str
    title: str
    description: str
    steps: list[Step]


def plan_history(catalog, schedules, targets, *, count: int, seed: int, now: dt.datetime):
    """Plans for `count` tickets created over the last six months, oldest first.

    schedules: organization id -> Schedule. targets: (organization id, priority) ->
    (first response, resolution) business minutes of the policy that applies.
    """
    return _Planner(catalog, schedules, targets, random.Random(seed), now).plan(count)


class _Planner:
    def __init__(self, catalog, schedules, targets, rng, now):
        self.catalog = catalog
        self.schedules = schedules
        self.targets = targets
        self.rng = rng
        self.now = now
        self.start = now - HISTORY
        self.retire_at = now - RETIRED_BEFORE_NOW

    def plan(self, count: int) -> list[TicketPlan]:
        arrivals = []
        organizations = self.catalog.organizations
        shares = [ORGANIZATION_SHARE[organization.slug] for organization in organizations]
        for organization, n in zip(organizations, _split(count, shares), strict=True):
            arrivals += [(at, organization) for at in self._arrivals(organization, n)]
        # Oldest first: creation takes the next number of the client, so numbers
        # and creation times end up in the same order.
        arrivals.sort(key=lambda arrival: arrival[0])
        return [self._ticket(organization, at) for at, organization in arrivals]

    # When tickets arrive

    def _arrivals(self, organization, n: int) -> list[dt.datetime]:
        schedule = self.schedules[organization.pk]
        always_open = bool(schedule.weekly_slots[5])
        zone = schedule.timezone
        first = self.start.astimezone(zone).date()
        last = self.now.astimezone(zone).date()
        days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
        weights = [
            (ALWAYS_OPEN_WEEKEND_WEIGHT if always_open and day.weekday() >= 5 else 1)
            * WEEKDAY_WEIGHTS[day.weekday()]
            * (1 + (GROWTH - 1) * i / len(days))
            for i, day in enumerate(days)
        ]
        hours = ALWAYS_OPEN_HOUR_WEIGHTS if always_open else OFFICE_HOUR_WEIGHTS

        instants = []
        while len(instants) < n:
            day = self.rng.choices(days, weights)[0]
            hour = self.rng.choices(range(24), hours)[0]
            local = dt.datetime.combine(day, dt.time(hour, self.rng.randrange(60)), zone)
            at = local.astimezone(dt.UTC)
            # The first and last day are partly outside the period: draw again.
            if self.start <= at <= self.now:
                instants.append(at)
        return instants

    # What happens to each ticket

    def _ticket(self, organization, created: dt.datetime) -> TicketPlan:
        rng = self.rng
        schedule = self.schedules[organization.pk]
        requester = rng.choice(self._eligible(self.catalog.clients[organization.pk], created))
        category = rng.choice(self._eligible(self.catalog.categories, created))
        priority = rng.choices(list(PRIORITY_WEIGHTS), list(PRIORITY_WEIGHTS.values()))[0]
        title, description = rng.choice(texts.TICKETS[category.name])
        first_target, resolution_target = self.targets[(organization.pk, priority)]

        steps = []

        def business(at, minutes):
            return add_business_minutes(at, max(1, round(minutes)), schedule)

        def wall(at, minutes):
            return at + dt.timedelta(minutes=minutes)

        def comment(at, actor, choices, internal=False):
            steps.append(Step(at, COMMENT, actor, body=rng.choice(choices), internal=internal))

        def move(at, actor, status):
            steps.append(Step(at, TRANSITION, actor, status=status))

        # First response: an agent's public reply takes the ticket (new -> open,
        # assigned to them).
        agent = self._staff(created)
        at = business(created, first_target * self._lateness(*FIRST_RESPONSE_LATE))
        comment(at, agent, texts.FIRST_RESPONSES)

        if rng.random() < CHANCE["raise_priority_or_lower"]:
            at = business(at, rng.randint(5, 30))
            steps.append(Step(at, UPDATE, agent, changes={"priority": self._other(priority)}))
        if rng.random() < CHANCE["reassign"]:
            at = business(at, rng.randint(30, 240))
            colleague = self._staff(at, besides=agent)
            steps.append(Step(at, UPDATE, agent, changes={"assignee": colleague}))
            agent = colleague

        # Work left until the resolution, in business minutes. The clock is paused
        # while waiting for the client, so that wait does not count.
        work = resolution_target * self._lateness(*RESOLUTION_LATE)
        left = max(work - business_minutes_between(created, at, schedule), 15)

        if rng.random() < CHANCE["internal_note"]:
            comment(business(at, left * 0.2), agent, texts.INTERNAL_NOTES, internal=True)
            at = steps[-1].at
            left *= 0.8
        if rng.random() < CHANCE["ask_for_information"]:
            at = business(at, left * 0.4)
            left *= 0.6
            comment(at, agent, texts.INFO_REQUESTS)
            move(wall(at, 1), agent, Status.PENDING)
            if rng.random() < CHANCE["client_answers"]:
                # A client reply on a pending ticket resumes it (pending -> open).
                at = wall(at, rng.randint(60, 1800))
                comment(at, requester, texts.CLIENT_REPLIES)
            else:
                # No answer: the agent resolves it from pending after three days.
                left = 3 * 8 * 60

        at = business(at, left)
        comment(at, agent, texts.SOLUTIONS)
        move(wall(at, 1), agent, Status.RESOLVED)
        at = wall(at, 1)

        if rng.random() < CHANCE["reopened"]:
            at = wall(at, rng.randint(120, 3 * 24 * 60))
            comment(at, requester, texts.REOPEN_REASONS)
            move(wall(at, 1), requester, Status.OPEN)
            at = business(wall(at, 1), rng.randint(60, 480))
            comment(at, agent, texts.SOLUTIONS)
            move(wall(at, 1), agent, Status.RESOLVED)
            at = wall(at, 1)

        if rng.random() < CHANCE["client_closes"]:
            at = wall(at, rng.randint(60, 4 * 24 * 60))
            if rng.random() < CHANCE["thanks"]:
                comment(at, requester, texts.THANKS)
            move(wall(at, 1), requester, Status.CLOSED)
        else:
            # The hourly auto-close job, some minutes after the seven days.
            move(
                at + AUTO_CLOSE_AFTER + dt.timedelta(minutes=rng.randint(1, 60)),
                None,
                Status.CLOSED,
            )

        return TicketPlan(
            organization=organization,
            created_at=created,
            requester=requester,
            category=category,
            priority=priority,
            title=title,
            description=description,
            # Only what has happened by now.
            steps=list(takewhile(lambda step: step.at <= self.now, steps)),
        )

    # Choices

    def _lateness(self, late_share: float, worst: float) -> float:
        """Fraction of the target used: within it, or past it for late_share."""
        if self.rng.random() < late_share:
            return self.rng.uniform(1.05, worst)
        return self.rng.uniform(0.1, 0.85)

    def _eligible(self, candidates, at: dt.datetime):
        """Inactive people and categories only before they were retired."""
        cutoff = self.retire_at - LAST_TICKET_BEFORE_RETIRING
        return [item for item in candidates if item.is_active or at < cutoff]

    def _staff(self, at: dt.datetime, besides=None):
        if self.rng.random() < CHANCE["admin_takes_it"] and besides is not self.catalog.admin:
            return self.catalog.admin
        agents = [agent for agent in self._eligible(self.catalog.agents, at) if agent != besides]
        return self.rng.choice(agents)

    def _other(self, priority: str) -> str:
        """A neighbouring priority, usually higher: the client underestimated it."""
        i = PRIORITY_ORDER.index(priority)
        up = i + 1 < len(PRIORITY_ORDER) and (i == 0 or self.rng.random() < 0.7)
        return PRIORITY_ORDER[i + 1 if up else i - 1]


def _split(total: int, shares: list[float]) -> list[int]:
    """Whole numbers proportional to shares that add up to total."""
    counts = [int(total * share) for share in shares]
    counts[0] += total - sum(counts)
    return counts
