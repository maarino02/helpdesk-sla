from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model, defined before the first migration.

    Swapping the user model after migrating is not supported by Django, so it
    exists from day one even though it adds nothing yet. Role and organization
    fields arrive with the data model (phase 2).
    """

    class Meta(AbstractUser.Meta):
        # "user" is a reserved word in PostgreSQL: unquoted, it means current_user.
        db_table = "app_user"
