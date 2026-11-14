import factory

from apps.accounts.models import User


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        # Reuse the row if the username already exists instead of failing.
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@example.com")
    # Hashed at build time with the test hasher (MD5, see settings.test).
    password = factory.django.Password("password")
