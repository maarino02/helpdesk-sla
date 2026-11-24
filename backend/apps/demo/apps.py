from django.apps import AppConfig


class DemoConfig(AppConfig):
    """Demo data (the seed_demo command). No models: it only writes through the
    other apps, so it can be removed without touching the schema."""

    name = "apps.demo"
    label = "demo"
    verbose_name = "Datos demo"
