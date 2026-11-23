import factory

from apps.organizations.models import Organization
from apps.sla.tests.factories import AlwaysOpenCalendarFactory


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Client {n}")
    slug = factory.Sequence(lambda n: f"client-{n}")
    # 24/7: ticket tests get business time equal to wall-clock time.
    business_calendar = factory.SubFactory(AlwaysOpenCalendarFactory)
