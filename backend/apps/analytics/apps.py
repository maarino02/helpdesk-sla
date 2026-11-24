from django.apps import AppConfig


class AnalyticsConfig(AppConfig):
    """Dashboard metrics: hand-written SQL in sql/, served read-only by the API.
    No models: every query reads the tickets app's tables."""

    name = "apps.analytics"
    label = "analytics"
    verbose_name = "Analítica"
