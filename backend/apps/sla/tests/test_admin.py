import pytest
from django.urls import reverse

from apps.accounts.tests.factories import AdminUserFactory, AgentUserFactory
from apps.organizations.tests.factories import OrganizationFactory
from apps.sla.models import BusinessCalendar, BusinessHours, Priority, SlaPolicy, Weekday
from apps.sla.tests.factories import (
    BusinessHoursFactory,
    HolidayFactory,
    SlaPolicyFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client):
    client.force_login(AdminUserFactory(is_staff=True, is_superuser=True))
    return client


@pytest.fixture
def agent_client(client):
    client.force_login(AgentUserFactory(is_staff=True, is_superuser=True))
    return client


def calendar_form(slots=(), holidays=(), initial_slots=(), name="Madrid"):
    """POST data for the calendar page: the calendar and its two inline formsets.

    initial_slots are existing BusinessHours rows, sent back as the first forms
    with their id, as the browser does.
    """
    data = {
        "name": name,
        "timezone": "Europe/Madrid",
        "business_hours-TOTAL_FORMS": len(initial_slots) + len(slots),
        "business_hours-INITIAL_FORMS": len(initial_slots),
        "holidays-TOTAL_FORMS": len(holidays),
        "holidays-INITIAL_FORMS": 0,
    }
    all_slots = [
        (s.pk, s.weekday, f"{s.start_time:%H:%M}", f"{s.end_time:%H:%M}") for s in initial_slots
    ]
    all_slots += [(None, *slot) for slot in slots]
    for i, (pk, weekday, start, end) in enumerate(all_slots):
        data[f"business_hours-{i}-weekday"] = weekday
        data[f"business_hours-{i}-start_time"] = start
        data[f"business_hours-{i}-end_time"] = end
        if pk is not None:
            data[f"business_hours-{i}-id"] = pk
    for i, (date, holiday_name) in enumerate(holidays):
        data[f"holidays-{i}-date"] = date
        data[f"holidays-{i}-name"] = holiday_name
    return data


@pytest.mark.parametrize("model", ["businesscalendar", "slapolicy"])
def test_changelist_renders(admin_client, model):
    BusinessHoursFactory()
    HolidayFactory()
    SlaPolicyFactory()

    response = admin_client.get(reverse(f"admin:sla_{model}_changelist"))

    assert response.status_code == 200


def test_admin_creates_calendar_with_slots_and_holidays(admin_client):
    response = admin_client.post(
        reverse("admin:sla_businesscalendar_add"),
        calendar_form(
            slots=[(Weekday.MONDAY, "09:00", "14:00"), (Weekday.MONDAY, "15:00", "18:00")],
            holidays=[("2026-12-25", "Navidad")],
        ),
    )

    assert response.status_code == 302
    calendar = BusinessCalendar.objects.get(name="Madrid")
    assert calendar.business_hours.count() == 2
    assert calendar.holidays.get().name == "Navidad"


def test_adjacent_slots_do_not_overlap(admin_client):
    response = admin_client.post(
        reverse("admin:sla_businesscalendar_add"),
        calendar_form(
            slots=[(Weekday.MONDAY, "09:00", "14:00"), (Weekday.MONDAY, "14:00", "18:00")]
        ),
    )

    assert response.status_code == 302


def test_overlapping_new_slots_show_a_form_error(admin_client):
    response = admin_client.post(
        reverse("admin:sla_businesscalendar_add"),
        calendar_form(
            slots=[(Weekday.MONDAY, "09:00", "14:00"), (Weekday.MONDAY, "13:00", "18:00")]
        ),
    )

    assert response.status_code == 200  # The form again, not an IntegrityError.
    assert "Las franjas del lunes se solapan: 09:00-14:00 y 13:00-18:00." in (
        response.content.decode()
    )
    assert not BusinessCalendar.objects.exists()


def test_new_slot_overlapping_an_existing_one_shows_a_form_error(admin_client):
    existing = BusinessHoursFactory(weekday=Weekday.TUESDAY)  # 09:00-18:00
    calendar = existing.calendar

    response = admin_client.post(
        reverse("admin:sla_businesscalendar_change", args=[calendar.pk]),
        calendar_form(
            name=calendar.name,
            initial_slots=[existing],
            slots=[(Weekday.TUESDAY, "17:00", "19:00")],
        ),
    )

    assert response.status_code == 200
    assert "se solapan" in response.content.decode()
    assert BusinessHours.objects.filter(calendar=calendar).count() == 1


def test_slot_marked_for_deletion_is_ignored(admin_client):
    existing = BusinessHoursFactory(weekday=Weekday.TUESDAY)  # 09:00-18:00
    calendar = existing.calendar
    data = calendar_form(
        name=calendar.name,
        initial_slots=[existing],
        slots=[(Weekday.TUESDAY, "10:00", "14:00")],
    )
    data["business_hours-0-DELETE"] = "on"

    response = admin_client.post(
        reverse("admin:sla_businesscalendar_change", args=[calendar.pk]), data
    )

    assert response.status_code == 302
    assert calendar.business_hours.get().start_time.hour == 10


def test_invalid_timezone_shows_a_form_error(admin_client):
    data = calendar_form()
    data["timezone"] = "Madrid"

    response = admin_client.post(reverse("admin:sla_businesscalendar_add"), data)

    assert response.status_code == 200
    assert "no es una zona horaria IANA válida" in response.content.decode()


def test_calendar_in_use_cannot_be_deleted(admin_client):
    organization = OrganizationFactory()

    response = admin_client.post(
        reverse("admin:sla_businesscalendar_delete", args=[organization.business_calendar_id]),
        {"post": "yes"},
    )

    # Django shows the protected objects instead of deleting.
    assert response.status_code == 200
    assert BusinessCalendar.objects.filter(pk=organization.business_calendar_id).exists()


def test_admin_creates_default_policy(admin_client):
    response = admin_client.post(
        reverse("admin:sla_slapolicy_add"),
        {"priority": Priority.HIGH, "first_response_minutes": 60, "resolution_minutes": 480},
    )

    assert response.status_code == 302
    assert SlaPolicy.objects.get(organization=None, priority=Priority.HIGH)


def test_second_default_policy_for_a_priority_shows_a_form_error(admin_client):
    # UNIQUE NULLS NOT DISTINCT: two default (NULL) policies for one priority clash.
    SlaPolicyFactory(priority=Priority.HIGH)

    response = admin_client.post(
        reverse("admin:sla_slapolicy_add"),
        {"priority": Priority.HIGH, "first_response_minutes": 60, "resolution_minutes": 480},
    )

    assert response.status_code == 200
    assert SlaPolicy.objects.filter(priority=Priority.HIGH).count() == 1


def test_default_policies_are_labelled(admin_client):
    SlaPolicyFactory()

    response = admin_client.get(reverse("admin:sla_slapolicy_changelist"))

    assert "Por defecto" in response.content.decode()


@pytest.mark.parametrize("model", ["businesscalendar", "slapolicy"])
def test_agent_cannot_add(agent_client, model):
    response = agent_client.get(reverse(f"admin:sla_{model}_add"))

    assert response.status_code == 403


def test_agent_sees_calendar_without_editable_inlines(agent_client):
    slot = BusinessHoursFactory()

    response = agent_client.get(
        reverse("admin:sla_businesscalendar_change", args=[slot.calendar_id])
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert 'name="_save"' not in content
    assert 'name="business_hours-0-start_time"' not in content
