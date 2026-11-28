import datetime
from pathlib import Path
from smtplib import SMTPException

import pytest
from django.core import mail
from django.db import transaction

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory, ClientUserFactory
from apps.tickets import tasks
from apps.tickets.models import AlertKind, AlertLevel, EventType, SlaAlert, Status, TicketEvent
from apps.tickets.services.notifications import (
    SLA_ALERT,
    STATUS_CHANGE,
    build_message,
    notify_on_commit,
    recipient_ids,
    suppressed,
    ticket_url,
)
from apps.tickets.services.state_machine import transition
from apps.tickets.tests.factories import SlaAlertFactory, TicketFactory
from config.celery import app

pytestmark = pytest.mark.django_db


@pytest.fixture
def enqueued(monkeypatch):
    """Calls to notify.delay, instead of sending them to the broker."""
    calls = []
    monkeypatch.setattr(tasks.notify, "delay", lambda *args: calls.append(args))
    return calls


def html_of(message):
    """The HTML alternative of an email (there must be exactly one)."""
    [(content, mimetype)] = message.alternatives
    assert mimetype == "text/html"
    return content


def status_event(ticket, to_status, actor=None):
    return TicketEvent.objects.create(
        ticket=ticket,
        organization=ticket.organization,
        actor=actor,
        event_type=EventType.STATUS_CHANGED,
        from_value=Status.OPEN,
        to_value=to_status,
    )


# Recipients of status changes


@pytest.mark.parametrize("to_status", [Status.PENDING, Status.RESOLVED, Status.CLOSED])
def test_requester_hears_about_pending_resolved_and_closed(to_status):
    ticket = TicketFactory()
    event = status_event(ticket, to_status, actor=AgentUserFactory())

    assert recipient_ids(STATUS_CHANGE, event.pk) == [ticket.requester_id]


def test_other_status_changes_notify_nobody():
    event = status_event(TicketFactory(), Status.OPEN, actor=AgentUserFactory())

    assert recipient_ids(STATUS_CHANGE, event.pk) == []


def test_requester_is_not_told_about_their_own_change():
    ticket = TicketFactory()
    event = status_event(ticket, Status.CLOSED, actor=ticket.requester)

    assert recipient_ids(STATUS_CHANGE, event.pk) == []


def test_colleague_closing_the_ticket_notifies_the_requester():
    ticket = TicketFactory()
    colleague = ClientUserFactory(organization=ticket.organization)
    event = status_event(ticket, Status.CLOSED, actor=colleague)

    assert recipient_ids(STATUS_CHANGE, event.pk) == [ticket.requester_id]


def test_inactive_requester_is_not_notified():
    ticket = TicketFactory(requester__is_active=False)
    event = status_event(ticket, Status.RESOLVED, actor=AgentUserFactory())

    assert recipient_ids(STATUS_CHANGE, event.pk) == []


def test_other_event_types_notify_nobody():
    ticket = TicketFactory()
    event = TicketEvent.objects.create(
        ticket=ticket, organization=ticket.organization, event_type=EventType.CREATED
    )

    assert recipient_ids(STATUS_CHANGE, event.pk) == []


# Recipients of SLA alerts


def test_alert_goes_to_the_assignee():
    agent = AgentUserFactory()
    AdminUserFactory()
    alert = SlaAlertFactory(ticket__assignee=agent)

    assert recipient_ids(SLA_ALERT, alert.pk) == [agent.pk]


def test_unassigned_alert_goes_to_every_active_admin():
    admins = [AdminUserFactory(), AdminUserFactory()]
    AdminUserFactory(is_active=False)
    AgentUserFactory()
    alert = SlaAlertFactory()

    assert recipient_ids(SLA_ALERT, alert.pk) == sorted(a.pk for a in admins)


def test_alert_for_an_inactive_assignee_goes_to_the_admins():
    admin = AdminUserFactory()
    alert = SlaAlertFactory(ticket__assignee=AgentUserFactory(is_active=False))

    assert recipient_ids(SLA_ALERT, alert.pk) == [admin.pk]


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="Unknown notification kind"):
        recipient_ids("comment", 1)


# Messages


def test_resolved_email_names_the_ticket_and_the_auto_close_window():
    ticket = TicketFactory(title="Impresora & escáner")
    event = status_event(ticket, Status.RESOLVED, actor=AgentUserFactory())

    message = build_message(STATUS_CHANGE, event.pk, ticket.requester_id)

    assert message.to == [ticket.requester.email]
    # Plain text: no HTML escaping of the title.
    assert message.subject == f"[{ticket.reference}] Incidencia resuelta: Impresora & escáner"
    assert ticket.requester.full_name in message.body
    assert "se cerrará automáticamente dentro de 7 días" in message.body
    assert "se cerrará automáticamente dentro de 7 días" in html_of(message)


def test_pending_email_asks_for_information():
    ticket = TicketFactory()
    event = status_event(ticket, Status.PENDING, actor=AgentUserFactory())

    message = build_message(STATUS_CHANGE, event.pk, ticket.requester_id)

    assert message.subject.startswith(f"[{ticket.reference}] Necesitamos más información")


def test_auto_close_email_says_so():
    ticket = TicketFactory()
    event = status_event(ticket, Status.CLOSED, actor=None)

    message = build_message(STATUS_CHANGE, event.pk, ticket.requester_id)

    assert message.subject.startswith(f"[{ticket.reference}] Incidencia cerrada")
    assert "se ha cerrado automáticamente tras 7 días" in message.body
    assert "se ha cerrado automáticamente tras 7 días" in html_of(message)


def test_alert_email_shows_the_deadline_in_the_business_time_zone():
    agent = AgentUserFactory()
    due_at = datetime.datetime(2026, 11, 24, 10, 0, tzinfo=datetime.UTC)
    alert = SlaAlertFactory(
        ticket__assignee=agent,
        level=AlertLevel.BREACHED,
        kind=AlertKind.FIRST_RESPONSE,
        due_at=due_at,
    )
    timezone_name = alert.ticket.organization.business_calendar.timezone
    assert timezone_name == "Europe/Madrid"  # UTC+1 in November

    message = build_message(SLA_ALERT, alert.pk, agent.pk)

    assert message.subject == (
        f"[{alert.ticket.reference}] SLA incumplido (primera respuesta): {alert.ticket.title}"
    )
    assert "que vencía el 24/11/2026 11:00 (Europe/Madrid)" in message.body
    assert "que vencía el 24/11/2026 11:00 (Europe/Madrid)" in html_of(message)
    assert "como administrador" not in message.body
    assert "como administrador" not in html_of(message)


def test_alert_email_to_an_admin_explains_why():
    admin = AdminUserFactory()
    alert = SlaAlertFactory()

    message = build_message(SLA_ALERT, alert.pk, admin.pk)

    assert message.subject.startswith(f"[{alert.ticket.reference}] SLA en riesgo (resolución)")
    assert "como administrador" in message.body
    assert "como administrador" in html_of(message)


def status_change_message(ticket):
    event = status_event(ticket, Status.RESOLVED, actor=AgentUserFactory())
    return build_message(STATUS_CHANGE, event.pk, ticket.requester_id)


def sla_alert_message(ticket):
    alert = SlaAlertFactory(ticket=ticket)
    return build_message(SLA_ALERT, alert.pk, AdminUserFactory().pk)


EVERY_KIND = pytest.mark.parametrize(
    "make_message", [status_change_message, sla_alert_message], ids=[STATUS_CHANGE, SLA_ALERT]
)


@EVERY_KIND
def test_every_email_links_to_the_ticket_page(make_message, settings):
    settings.SITE_URL = "https://helpdesk.example.com"
    ticket = TicketFactory()

    message = make_message(ticket)

    url = f"https://helpdesk.example.com/tickets/{ticket.pk}/"
    assert ticket_url(ticket) == url
    assert f"Ver la incidencia: {url}" in message.body
    assert f'href="{url}"' in html_of(message)


@EVERY_KIND
def test_html_escapes_what_users_write_and_text_keeps_it_as_is(make_message):
    title = '<img src="x" onerror="alert(1)"> & <a href="https://phishing.example">'
    ticket = TicketFactory(title=title)

    message = make_message(ticket)

    html = html_of(message)
    assert "<img" not in html
    assert '<a href="https://phishing' not in html  # The page's own link is a real <a>.
    assert "&lt;img src=&quot;x&quot; onerror=&quot;alert(1)&quot;&gt; &amp; &lt;a" in html
    # Plain text is never interpreted as markup, so escaping it would only garble it.
    assert title in message.body


def test_no_html_email_template_turns_autoescape_off():
    # The test above only covers the variables it renders; this guard covers new
    # templates and new variables too.
    templates = Path(__file__).resolve().parents[1] / "templates" / "tickets" / "email"
    html_templates = sorted(templates.glob("*.html"))

    assert html_templates  # The glob must find something, or the guard checks nothing.
    for template in html_templates:
        assert "autoescape" not in template.read_text(encoding="utf-8"), template.name


# Enqueueing after the commit


def test_transition_enqueues_the_notification_after_the_commit(
    enqueued, django_capture_on_commit_callbacks
):
    ticket = TicketFactory(status=Status.OPEN)

    with django_capture_on_commit_callbacks(execute=True):
        transition(ticket, Status.RESOLVED, AgentUserFactory())
        assert enqueued == []  # not before the commit

    event = ticket.events.get(event_type=EventType.STATUS_CHANGED)
    assert enqueued == [(STATUS_CHANGE, event.pk)]


def test_transition_to_an_unnotified_status_enqueues_nothing(
    enqueued, django_capture_on_commit_callbacks
):
    ticket = TicketFactory(status=Status.PENDING)

    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        transition(ticket, Status.OPEN, AgentUserFactory())

    assert callbacks == []


def test_rolled_back_transition_enqueues_nothing(enqueued, django_capture_on_commit_callbacks):
    ticket = TicketFactory(status=Status.OPEN)
    agent = AgentUserFactory()

    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        transition(ticket, Status.RESOLVED, agent)
        raise RuntimeError("the request fails after the transition")

    assert callbacks == []
    assert enqueued == []


def test_auto_close_notifies_through_the_transition(enqueued, django_capture_on_commit_callbacks):
    ticket = TicketFactory(
        resolved=True,
        resolved_at=datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(days=8),
    )

    with django_capture_on_commit_callbacks(execute=True):
        tasks.auto_close_resolved()

    event = ticket.events.get(event_type=EventType.STATUS_CHANGED, to_value=Status.CLOSED)
    assert enqueued == [(STATUS_CHANGE, event.pk)]


def test_alert_job_enqueues_one_notification_per_new_alert(
    enqueued, django_capture_on_commit_callbacks
):
    past = datetime.datetime.now(tz=datetime.UTC) - datetime.timedelta(hours=1)
    TicketFactory(
        resolution_warning_at=past,
        resolution_due_at=past,
        first_response_warning_at=past,
        first_response_due_at=past,
    )

    with django_capture_on_commit_callbacks(execute=True):
        tasks.raise_sla_alerts()
    with django_capture_on_commit_callbacks(execute=True):
        tasks.raise_sla_alerts()  # nothing new: nothing enqueued

    alert_ids = {alert_id for kind, alert_id in enqueued if kind == SLA_ALERT}
    assert len(enqueued) == 2  # resolution and first response breached
    assert alert_ids == set(SlaAlert.objects.values_list("pk", flat=True))


def test_notify_on_commit_rejects_unknown_kinds():
    with pytest.raises(ValueError, match="Unknown notification kind"):
        notify_on_commit("comment", 1)


# Suppression (the demo seed writes past history that nobody must hear about)


def test_nothing_is_enqueued_inside_suppressed(enqueued, django_capture_on_commit_callbacks):
    ticket = TicketFactory(status=Status.OPEN)

    with django_capture_on_commit_callbacks(execute=True) as callbacks, suppressed():
        transition(ticket, Status.RESOLVED, AgentUserFactory())

    assert callbacks == []
    assert enqueued == []


def test_suppression_is_decided_when_registering_not_when_committing(
    enqueued, django_capture_on_commit_callbacks
):
    # The commit happens after the block ends, as when a seed step runs inside
    # an outer transaction: it must still send nothing.
    with django_capture_on_commit_callbacks(execute=True):
        with suppressed():
            notify_on_commit(STATUS_CHANGE, 1)
        assert enqueued == []  # not committed yet

    assert enqueued == []


def test_notifications_resume_after_the_block(enqueued, django_capture_on_commit_callbacks):
    with pytest.raises(RuntimeError), suppressed():
        raise RuntimeError("the seed fails halfway")

    with django_capture_on_commit_callbacks(execute=True):
        notify_on_commit(STATUS_CHANGE, 1)

    assert enqueued == [(STATUS_CHANGE, 1)]


# Tasks


def test_notify_fans_out_one_send_per_recipient(monkeypatch):
    sent = []
    monkeypatch.setattr(tasks.send_notification, "delay", lambda *args: sent.append(args))
    admins = [AdminUserFactory(), AdminUserFactory()]
    alert = SlaAlertFactory()

    assert tasks.notify(SLA_ALERT, alert.pk) == 2
    assert sent == [(SLA_ALERT, alert.pk, admin.pk) for admin in sorted(admins, key=lambda a: a.pk)]


def test_send_notification_sends_one_email():
    ticket = TicketFactory()
    event = status_event(ticket, Status.RESOLVED, actor=AgentUserFactory())

    tasks.send_notification(STATUS_CHANGE, event.pk, ticket.requester_id)

    [message] = mail.outbox
    assert message.to == [ticket.requester.email]
    assert message.from_email == "HelpDesk SLA <no-reply@localhost>"
    assert html_of(message).startswith("<!doctype html>")


def test_send_notification_retries_smtp_and_network_errors_only():
    assert set(tasks.send_notification.autoretry_for) == {SMTPException, OSError}
    assert tasks.send_notification.max_retries == 5
    assert tasks.send_notification.retry_backoff is True


def test_tests_never_reach_a_real_broker():
    # production_like tests commit for real, so on_commit callbacks do run.
    assert app.conf.broker_url == "memory://"
