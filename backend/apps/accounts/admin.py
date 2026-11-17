from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .forms import UserChangeForm, UserCreationForm
from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    # The base class keeps password handling (hashing, change-password view); only
    # the fields that referred to username, first_name and last_name are replaced.
    form = UserChangeForm
    add_form = UserCreationForm

    list_display = ("email", "full_name", "role", "organization", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff", "is_superuser")
    # One query with a JOIN instead of one query per row for the organization column.
    list_select_related = ("organization",)
    search_fields = ("email", "full_name")
    ordering = ("email",)
    readonly_fields = ("last_login", "date_joined")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Datos personales", {"fields": ("full_name",)}),
        ("Rol", {"fields": ("role", "organization")}),
        (
            "Permisos",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Fechas", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "full_name",
                    "role",
                    "organization",
                    "usable_password",
                    "password1",
                    "password2",
                ),
            },
        ),
    )
