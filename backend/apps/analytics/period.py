"""The filters every metric takes: a period of whole days and, optionally, a client.

Days and months are those of the provider's time zone (ANALYTICS_TIME_ZONE), the
same for every client, so the staff dashboard can add clients up: a ticket
created at 23:30 on 31 March in Mexico City is already 1 April in Madrid, and
counts in April for everyone.
"""

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone
from rest_framework import serializers

from apps.accounts.models import Role
from apps.core.exceptions import ActionNotAllowed
from apps.organizations.models import Organization

DEFAULT_MONTHS = 12
# Bounds the cost of a request: three years of tickets at most.
MAX_DAYS = 3 * 366


class OtherOrganization(ActionNotAllowed):
    code = "other_organization"

    def __init__(self):
        super().__init__("Solo puedes consultar las métricas de tu organización.")


@dataclass(frozen=True)
class Period:
    start: dt.date  # first day, included
    end: dt.date  # last day, included
    organization_id: int | None  # None: every client (staff only)
    time_zone: ZoneInfo

    def params(self) -> dict:
        """Query parameters. The end bound is exclusive: midnight after the last day,
        so a ticket at 23:59:59.9 on that day is in and one at midnight is not."""
        return {
            "start": _midnight(self.start, self.time_zone),
            "end": _midnight(self.end + dt.timedelta(days=1), self.time_zone),
            "org": self.organization_id,
            "tz": self.time_zone.key,
        }

    def describe(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "organization": self.organization_id,
            "time_zone": self.time_zone.key,
        }


class PeriodSerializer(serializers.Serializer):
    start = serializers.DateField(required=False)
    end = serializers.DateField(required=False)
    organization = serializers.IntegerField(required=False, min_value=1)

    def validate(self, attrs):
        zone = ZoneInfo(settings.ANALYTICS_TIME_ZONE)
        end = attrs.get("end") or timezone.now().astimezone(zone).date()
        # Default: the last twelve calendar months, the current one included.
        start = attrs.get("start") or _months_back(end, DEFAULT_MONTHS - 1)
        if start > end:
            raise serializers.ValidationError({"start": "El inicio es posterior al final."})
        if (end - start).days >= MAX_DAYS:
            raise serializers.ValidationError(
                {"start": f"El periodo no puede superar {MAX_DAYS} días."}
            )
        return {**attrs, "start": start, "end": end, "time_zone": zone}


def read_period(request) -> Period:
    """The period and client of the request, scoped to what the user may see."""
    serializer = PeriodSerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    return Period(
        start=data["start"],
        end=data["end"],
        organization_id=_scoped_organization(request.user, data.get("organization")),
        time_zone=data["time_zone"],
    )


def _scoped_organization(user, requested: int | None) -> int | None:
    """A client always gets their own organization; staff may pick one or none.

    Defense in depth: row-level security would hide other clients' rows anyway,
    but a client asking for another organization is told so instead of getting
    an empty (and misleading) answer.
    """
    if user.role == Role.CLIENT:
        if requested not in (None, user.organization_id):
            raise OtherOrganization()
        return user.organization_id
    if requested is not None and not Organization.objects.filter(pk=requested).exists():
        raise serializers.ValidationError({"organization": "La organización no existe."})
    return requested


def _midnight(day: dt.date, zone: ZoneInfo) -> dt.datetime:
    # combine() with tzinfo: on a daylight saving change, midnight still exists
    # in every zone we use (changes happen at 01:00-03:00).
    return dt.datetime.combine(day, dt.time(0), zone)


def _months_back(day: dt.date, months: int) -> dt.date:
    """The first day of the month `months` before day's month."""
    index = day.year * 12 + day.month - 1 - months
    return dt.date(index // 12, index % 12 + 1, 1)
