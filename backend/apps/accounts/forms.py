from django.contrib.auth import forms as auth_forms

from .models import User


# Django's user forms declare the "username" field in Meta; with an email login
# they have to be redeclared for this model.
# AdminUserCreationForm (Django 5.1+), not UserCreationForm: only the admin variant
# has the "usable_password" field that UserAdmin.add_fieldsets uses.
class UserCreationForm(auth_forms.AdminUserCreationForm):
    class Meta:
        model = User
        fields = ("email", "full_name", "role", "organization")


class UserChangeForm(auth_forms.UserChangeForm):
    class Meta:
        model = User
        fields = "__all__"
