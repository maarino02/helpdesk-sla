import factory

from apps.organizations.models import Organization
from apps.sla.tests.factories import BusinessCalendarFactory


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Client {n}")
    slug = factory.Sequence(lambda n: f"client-{n}")
    business_calendar = factory.SubFactory(BusinessCalendarFactory)
