from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower, Now


class Role(models.TextChoices):
    # varchar + CHECK, like Priority in the sla app (no native ENUM).
    CLIENT = "client", "Cliente"
    AGENT = "agent", "Agente"
    ADMIN = "admin", "Administrador"


class UserManager(BaseUserManager):
    @classmethod
    def normalize_email(cls, email):
        # The base class only lowercases the domain. We lowercase the whole address so
        # a plain UNIQUE (email) is enough for case-insensitive uniqueness; a CHECK in
        # the table guarantees no row is stored any other way.
        return (email or "").strip().lower()

    def get_by_natural_key(self, email):
        # Used by ModelBackend on login: "Ana@Acme.com" finds "ana@acme.com" with a
        # plain equality, so the unique index on email is used.
        return self.get(email=self.normalize_email(email))

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The email must be set.")
        user = self.model(email=self.normalize_email(email), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", Role.ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields["role"] != Role.ADMIN:
            raise ValueError("A superuser must have the admin role.")
        if extra_fields["is_staff"] is not True or extra_fields["is_superuser"] is not True:
            raise ValueError("A superuser must have is_staff=True and is_superuser=True.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Anyone using the system: client users, support agents and admins.

    Clients belong to an organization; agents and admins work for the support
    provider and have none. The database enforces that rule with a CHECK.
    """

    # Uniqueness is a Meta constraint, not unique=True, for the same reason as
    # Organization.slug: unique=True would add a varchar_pattern_ops index we never use.
    email = models.EmailField(max_length=254)
    full_name = models.CharField(max_length=150)
    role = models.CharField(max_length=20, choices=Role)
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        # Clients are deactivated, never deleted (see Organization.is_active).
        on_delete=models.PROTECT,
        related_name="users",
    )
    is_active = models.BooleanField(db_default=True)
    # Access to the Django admin (internal back office). Not the same as the "admin"
    # role, which is a business role used by the API.
    is_staff = models.BooleanField(db_default=False)
    date_joined = models.DateTimeField(db_default=Now())

    objects = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    # Asked by createsuperuser, besides USERNAME_FIELD and the password.
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        # "user" is a reserved word in PostgreSQL: unquoted, it means current_user.
        db_table = "app_user"
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"
        constraints = [
            models.UniqueConstraint(
                fields=["email"],
                name="app_user_unique_email",
                violation_error_message="Ya existe un usuario con ese email.",
            ),
            models.CheckConstraint(
                condition=Q(email=Lower("email")),
                name="app_user_email_lowercase",
                violation_error_message="El email debe guardarse en minúsculas.",
            ),
            models.CheckConstraint(
                condition=~Q(email=""),
                name="app_user_email_not_empty",
                violation_error_message="El email es obligatorio.",
            ),
            models.CheckConstraint(
                condition=Q(role__in=Role.values),
                name="app_user_role_valid",
            ),
            # (role = 'client') = (organization_id IS NOT NULL), written as the two
            # allowed cases: a client with an organization, or staff without one.
            models.CheckConstraint(
                condition=(
                    Q(role=Role.CLIENT, organization__isnull=False)
                    | (~Q(role=Role.CLIENT) & Q(organization__isnull=True))
                ),
                name="app_user_role_organization_consistent",
                violation_error_message=(
                    "Los clientes deben pertenecer a una organización; "
                    "agentes y administradores, a ninguna."
                ),
            ),
            models.CheckConstraint(
                condition=Q(is_staff=False) | ~Q(role=Role.CLIENT),
                name="app_user_client_not_staff",
                violation_error_message="Un cliente no puede acceder al admin.",
            ),
        ]

    def clean(self):
        super().clean()
        # Forms (e.g. the admin) do not go through the manager, so normalize here too.
        self.email = type(self).objects.normalize_email(self.email)

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name
