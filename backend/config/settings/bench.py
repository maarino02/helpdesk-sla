"""Development settings pointed at the measurement database (make bench-db).

Same server and roles as development, another database: helpdesk_bench holds the
volume seed (200,000 tickets) for query plans, while the development database
keeps the demo data. Use it with --settings=config.settings.bench.
"""

from .dev import *  # noqa: F403
from .dev import DATABASES

BENCH_DATABASE = "helpdesk_bench"

# A new dict: DATABASES is the same object in base.py and dev.py, and changing it in
# place would point those modules at this database too.
DATABASES = {alias: {**config, "NAME": BENCH_DATABASE} for alias, config in DATABASES.items()}
