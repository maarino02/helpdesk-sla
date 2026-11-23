"""update_ticket(): assignee, category and priority, with its SLA effects.

Tickets are created at NOW with the medium policy (first response 60, resolution
480). Organizations have a 24/7 calendar in the test data, so business time
equals wall-clock time and values are exact.
"""

from datetime import UTC, datetime, timedelta

import pytest
from django.core.exceptions import ValidationError

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.sla.models import Priority
from apps.sla.tests.factories import SlaPolicyFactory
from apps.tickets.models import EventType, Status, Ticket, TicketEvent
from apps.tickets.services.creation import create_ticket
from apps.tickets.services.state_machine import transition
from apps.tickets.services.triage import (
    PriorityLocked,
    StaffOnly,
    TicketClosed,
    update_ticket,
)
from apps.tickets.tests.factories import CategoryFactory, TicketFactory
from tests.rls import set_context

pytestmark = pytest.mark.django_db

NOW = datetime(2026, 11, 23, 10, 0, tzinfo=UTC)


def minutes(n):
    return NOW + timedelta(minutes=n)


def events(ticket, event_type):
    return list(TicketEvent.objects.filter(ticket=ticket, event_type=event_type))


@pytest.fixture
def agent():
    return AgentUserFactory()


@pytest.fixture
def ticket():
    for priority, first, resolution in [
        (Priority.LOW, 120, 960),
        (Priority.MEDIUM, 60, 480),
        (Priority.HIGH, 30, 240),
    ]:
        SlaPolicyFactory(
            priority=priority, first_response_minutes=first, resolution_minutes=resolution
        )
    return create_ticket(
        requester=ClientUserFactory(),
        title="Pantalla negra",
        description="No arranca.",
        category=CategoryFactory(),
        priority=Priority.MEDIUM,
        now=NOW,
    )


class TestPriority:
    def test_raising_shortens_both_deadlines(self, ticket, agent):
        updated = update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(20))

        assert updated.priority == Priority.HIGH
        assert updated.sla_policy.priority == Priority.HIGH
        assert updated.resolution_due_at == minutes(480 - 240)
        assert updated.resolution_target_minutes == 240
        # Computed again from creation: the first response clock never pauses.
        assert updated.first_response_due_at == minutes(30)

    def test_lowering_extends_both_deadlines(self, ticket, agent):
        updated = update_ticket(ticket, agent, priority=Priority.LOW, now=minutes(20))

        assert updated.resolution_due_at == minutes(960)
        assert updated.resolution_target_minutes == 960
        assert updated.first_response_due_at == minutes(120)

    def test_difference_is_from_the_ticket_target_not_the_edited_policy(self, ticket, agent):
        ticket.sla_policy.resolution_minutes = 600  # Edited after the ticket was created.
        ticket.sla_policy.save()

        updated = update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(20))

        # 240 - 480 (promised to this ticket), not 240 - 600 (the policy today).
        assert updated.resolution_due_at == minutes(240)

    def test_raising_can_breach_at_once(self, ticket, agent):
        updated = update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(300))

        # A more urgent ticket that should already be solved is overdue.
        assert updated.resolution_due_at == minutes(240)
        assert updated.resolution_due_at < minutes(300)

    def test_breached_deadlines_never_move(self, ticket, agent):
        updated = update_ticket(ticket, agent, priority=Priority.LOW, now=minutes(500))

        # Lowering the priority does not erase the breach of either deadline.
        assert updated.resolution_due_at == minutes(480)
        assert updated.resolution_target_minutes == 480
        assert updated.first_response_due_at == minutes(60)
        assert updated.priority == Priority.LOW
        [event] = events(ticket, EventType.PRIORITY_CHANGED)
        assert event.metadata == {"kept_breached": ["resolution", "first_response"]}

    def test_answered_first_response_deadline_is_history(self, ticket, agent):
        Ticket.objects.filter(pk=ticket.pk).update(
            first_response_at=minutes(15), first_response_business_minutes=15
        )

        updated = update_ticket(ticket, agent, priority=Priority.LOW, now=minutes(20))

        assert updated.first_response_due_at == minutes(60)
        assert updated.resolution_due_at == minutes(960)

    def test_paused_ticket_is_judged_by_when_the_clock_stopped(self, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.PENDING, agent, now=minutes(100))

        # Past the deadline in wall-clock time, but the clock stopped at 100.
        updated = update_ticket(ticket, agent, priority=Priority.LOW, now=minutes(600))

        assert updated.resolution_due_at == minutes(960)

    def test_resolution_minutes_stay_exact_after_a_change_and_a_pause(self, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.PENDING, agent, now=minutes(100))
        update_ticket(ticket, agent, priority=Priority.LOW, now=minutes(600))
        transition(ticket, Status.OPEN, agent, now=minutes(700))  # Paused 600 minutes.

        resolved = transition(ticket, Status.RESOLVED, agent, now=minutes(800))

        # consumed = target - left = 960 - (1560 - 800) = 200 = 800 - 600 paused.
        assert resolved.resolution_due_at == minutes(1560)
        assert resolved.resolution_business_minutes == 200

    def test_event_records_the_old_and_new_deadlines(self, ticket, agent):
        update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(20))

        [event] = events(ticket, EventType.PRIORITY_CHANGED)
        assert (event.from_value, event.to_value, event.actor) == ("medium", "high", agent)
        assert event.metadata == {
            "resolution_due_at": {
                "from": minutes(480).isoformat(),
                "to": minutes(240).isoformat(),
            },
            "first_response_due_at": {
                "from": minutes(60).isoformat(),
                "to": minutes(30).isoformat(),
            },
            "resolution_target_minutes": {"from": 480, "to": 240},
            "kept_breached": [],
        }

    def test_locked_once_resolved(self, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.RESOLVED, agent, now=minutes(100))

        with pytest.raises(PriorityLocked):
            update_ticket(ticket, agent, priority=Priority.LOW)

    def test_invalid_priority(self, ticket, agent):
        with pytest.raises(ValidationError) as exc:
            update_ticket(ticket, agent, priority="urgent")

        assert "priority" in exc.value.message_dict


class TestAssignee:
    def test_assign_and_unassign(self, ticket, agent):
        other = AdminUserFactory()

        update_ticket(ticket, agent, assignee=other)
        updated = update_ticket(ticket, agent, assignee=None)

        assert updated.assignee is None
        values = [(e.from_value, e.to_value) for e in events(ticket, EventType.ASSIGNEE_CHANGED)]
        assert values == [(None, str(other.pk)), (str(other.pk), None)]

    @pytest.mark.parametrize(
        "make_assignee",
        [ClientUserFactory, lambda: AgentUserFactory(is_active=False)],
        ids=["client", "inactive-agent"],
    )
    def test_only_active_staff(self, ticket, agent, make_assignee):
        with pytest.raises(ValidationError) as exc:
            update_ticket(ticket, agent, assignee=make_assignee())

        assert "assignee" in exc.value.message_dict

    def test_resolved_ticket_can_be_reassigned(self, ticket, agent):
        transition(ticket, Status.OPEN, agent, now=minutes(10))
        transition(ticket, Status.RESOLVED, agent, now=minutes(100))
        other = AgentUserFactory()

        assert update_ticket(ticket, agent, assignee=other).assignee == other


class TestCategory:
    def test_change_records_an_event(self, ticket, agent):
        old = ticket.category
        new = CategoryFactory()

        updated = update_ticket(ticket, agent, category=new)

        assert updated.category == new
        [event] = events(ticket, EventType.CATEGORY_CHANGED)
        assert (event.from_value, event.to_value) == (str(old.pk), str(new.pk))

    def test_inactive_category(self, ticket, agent):
        with pytest.raises(ValidationError) as exc:
            update_ticket(ticket, agent, category=CategoryFactory(is_active=False))

        assert "category" in exc.value.message_dict


class TestRules:
    def test_current_values_are_not_changes(self, ticket, agent):
        update_ticket(
            ticket,
            agent,
            assignee=None,
            priority=ticket.priority,
            category=ticket.category,
        )

        # Only the creation event.
        assert TicketEvent.objects.filter(ticket=ticket).count() == 1

    def test_omitted_fields_are_untouched(self, ticket, agent):
        update_ticket(ticket, agent, assignee=agent)

        updated = update_ticket(ticket, agent, priority=Priority.HIGH, now=minutes(20))

        assert updated.assignee == agent  # Not unassigned by omission.

    def test_closed_ticket_cannot_change(self, agent):
        ticket = TicketFactory(closed=True)

        with pytest.raises(TicketClosed):
            update_ticket(ticket, agent, assignee=agent)

    @pytest.mark.parametrize("make_actor", [ClientUserFactory, lambda: None])
    def test_staff_only(self, ticket, make_actor):
        with pytest.raises(StaffOnly):
            update_ticket(ticket, make_actor(), assignee=None)

    def test_rejected_update_changes_nothing(self, ticket, agent):
        # The assignee is valid, the category is not: all or nothing.
        with pytest.raises(ValidationError):
            update_ticket(ticket, agent, assignee=agent, category=CategoryFactory(is_active=False))

        assert Ticket.objects.get(pk=ticket.pk).assignee is None
        assert not events(ticket, EventType.ASSIGNEE_CHANGED)

    def test_works_in_the_agent_row_level_security_context(self, ticket, agent):
        set_context(role="agent", user_id=agent.pk)

        updated = update_ticket(
            ticket, agent, assignee=agent, priority=Priority.HIGH, now=minutes(20)
        )

        assert (updated.assignee, updated.priority) == (agent, Priority.HIGH)
