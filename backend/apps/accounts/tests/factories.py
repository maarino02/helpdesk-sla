import factory

from apps.accounts.models import Role, User
from apps.organizations.tests.factories import OrganizationFactory


class UserFactory(factory.django.DjangoModelFactory):
    """Base factory. Use the role-specific subclasses in tests."""

    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Sequence(lambda n: f"User {n}")
    # Hashed at build time with the test hasher (MD5, see settings.test).
    password = factory.django.Password("password")


class ClientUserFactory(UserFactory):
    role = Role.CLIENT
    organization = factory.SubFactory(OrganizationFactory)


class AgentUserFactory(UserFactory):
    role = Role.AGENT
    organization = None


class AdminUserFactory(UserFactory):
    role = Role.ADMIN
    organization = None
