"""config.settings.bench: the development settings on the measurement database."""

import importlib

from config.settings import base


def test_every_alias_points_at_the_bench_database_without_touching_the_others():
    before = {alias: config["NAME"] for alias, config in base.DATABASES.items()}

    bench = importlib.import_module("config.settings.bench")

    assert {config["NAME"] for config in bench.DATABASES.values()} == {"helpdesk_bench"}
    assert {alias: config["NAME"] for alias, config in base.DATABASES.items()} == before
