"""The fixed part of the demo: calendars, clients, SLA policies, categories and users.

Everything is declared as data at the top of the module, so the demo can be read
(and changed) without following any code. Names are fictitious and every email
uses the reserved .example domain (RFC 2606), so no message can reach a real
mailbox.
"""

import datetime
from dataclasses import dataclass

from apps.accounts.models import Role, User
from apps.organizations.models import Organization
from apps.sla.models import (
    END_OF_DAY,
    BusinessCalendar,
    BusinessHours,
    Holiday,
    Priority,
    SlaPolicy,
)
from apps.tickets.models import Category

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)


def hm(hours: int, minutes: int = 0) -> datetime.time:
    return datetime.time(hours, minutes)


# Calendars. Holidays are the fixed-date ones (and in Mexico, the "nth Monday"
# ones): holidays tied to Easter are left out to keep the rules simple.

CALENDARS = {
    "madrid": {
        "name": "Oficina Madrid",
        "timezone": "Europe/Madrid",
        # Split shift Monday to Thursday, intensive shift on Friday.
        "hours": [
            *[(day, hm(9), hm(14)) for day in (MON, TUE, WED, THU)],
            *[(day, hm(15), hm(18)) for day in (MON, TUE, WED, THU)],
            (FRI, hm(8), hm(15)),
        ],
        "holidays": [
            ((1, 1), "Año Nuevo"),
            ((1, 6), "Epifanía del Señor"),
            ((5, 1), "Fiesta del Trabajo"),
            ((5, 2), "Fiesta de la Comunidad de Madrid"),
            ((5, 15), "San Isidro"),
            ((8, 15), "Asunción de la Virgen"),
            ((10, 12), "Fiesta Nacional de España"),
            ((11, 1), "Todos los Santos"),
            ((11, 9), "Nuestra Señora de la Almudena"),
            ((12, 6), "Día de la Constitución"),
            ((12, 8), "Inmaculada Concepción"),
            ((12, 25), "Navidad"),
        ],
    },
    "cdmx": {
        "name": "Oficina Ciudad de México",
        "timezone": "America/Mexico_City",
        "hours": [(day, hm(9), hm(18)) for day in (MON, TUE, WED, THU, FRI)],
        "holidays": [
            ((1, 1), "Año Nuevo"),
            # (month, weekday, n): the nth given weekday of the month.
            ((2, MON, 1), "Día de la Constitución"),
            ((3, MON, 3), "Natalicio de Benito Juárez"),
            ((5, 1), "Día del Trabajo"),
            ((9, 16), "Día de la Independencia"),
            ((11, MON, 3), "Día de la Revolución"),
            ((12, 25), "Navidad"),
        ],
    },
    "always": {
        "name": "24x7",
        "timezone": "UTC",
        # 00:00-00:00 is a whole day (the end 00:00 means 24:00).
        "hours": [(day, END_OF_DAY, END_OF_DAY) for day in range(7)],
        "holidays": [],
    },
}

ORGANIZATIONS = [
    {"name": "Ferretería Alameda", "slug": "alameda", "calendar": "madrid"},
    {"name": "Transportes del Pacífico", "slug": "pacifico", "calendar": "cdmx"},
    {"name": "Clínica Vallebueno", "slug": "vallebueno", "calendar": "always"},
]

# Business minutes. The default policies (no organization) apply to every client
# without an agreement of its own.
DEFAULT_POLICIES = {
    Priority.CRITICAL: (30, 240),
    Priority.HIGH: (60, 480),
    Priority.MEDIUM: (240, 1440),
    Priority.LOW: (480, 2880),
}
# A client-specific agreement overrides the default for those priorities only.
CLIENT_POLICIES = {
    "vallebueno": {
        Priority.CRITICAL: (15, 120),
        Priority.HIGH: (30, 240),
    },
}

CATEGORIES = [
    ("Accesos y contraseñas", True),
    ("Correo electrónico", True),
    ("Red y VPN", True),
    ("Equipos y periféricos", True),
    ("Aplicaciones", True),
    ("Facturación", True),
    # Retired: old tickets keep it, new ones cannot use it.
    ("Telefonía fija", False),
]

STAFF_DOMAIN = "soporte.example"

# (email, full name, role, active). The first agent is the documented demo account.
STAFF = [
    ("admin", "Marta Ibáñez", Role.ADMIN, True),
    ("agente", "Laura Gil", Role.AGENT, True),
    ("carlos.ruiz", "Carlos Ruiz", Role.AGENT, True),
    ("nuria.campos", "Nuria Campos", Role.AGENT, True),
    ("diego.ferrer", "Diego Ferrer", Role.AGENT, True),
    # Left the company: still the assignee of old tickets.
    ("pablo.santos", "Pablo Santos", Role.AGENT, False),
]

# Per organization slug: (email, full name, active). The first one is the
# documented demo account of that client.
CLIENTS = {
    "alameda": [
        ("cliente", "Javier Moreno", True),
        ("rosa.vidal", "Rosa Vidal", True),
        ("andres.prieto", "Andrés Prieto", True),
        ("elena.soto", "Elena Soto", False),
    ],
    "pacifico": [
        ("cliente", "Fernanda Castillo", True),
        ("ricardo.mendoza", "Ricardo Mendoza", True),
        ("gabriela.ortiz", "Gabriela Ortiz", True),
    ],
    "vallebueno": [
        ("cliente", "Sergio Navarro", True),
        ("lucia.herrera", "Lucía Herrera", True),
    ],
}


@dataclass
class Catalog:
    """What the history generator needs from the catalog."""

    organizations: list[Organization]
    categories: list[Category]
    admin: User
    agents: list[User]
    clients: dict[int, list[User]]  # by organization id


def nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime.date:
    first = datetime.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + datetime.timedelta(days=offset + 7 * (n - 1))


def holiday_date(rule: tuple, year: int) -> datetime.date:
    if len(rule) == 2:
        month, day = rule
        return datetime.date(year, month, day)
    month, weekday, n = rule
    return nth_weekday(year, month, weekday, n)


def create_catalog(*, password_hash: str, years: range) -> Catalog:
    """Create the catalog. Needs a context that may write every table (system).

    password_hash: every user gets the same one, hashed once by the caller
    (hashing per user would take ~0.3 s each). years: the years that get
    holidays, so the history and the coming months have them.
    """
    calendars = {key: _create_calendar(spec, years) for key, spec in CALENDARS.items()}

    organizations = [
        Organization.objects.create(
            name=spec["name"], slug=spec["slug"], business_calendar=calendars[spec["calendar"]]
        )
        for spec in ORGANIZATIONS
    ]
    by_slug = {organization.slug: organization for organization in organizations}

    SlaPolicy.objects.bulk_create(
        [
            SlaPolicy(
                priority=priority, first_response_minutes=first, resolution_minutes=resolution
            )
            for priority, (first, resolution) in DEFAULT_POLICIES.items()
        ]
        + [
            SlaPolicy(
                organization=by_slug[slug],
                priority=priority,
                first_response_minutes=first,
                resolution_minutes=resolution,
            )
            for slug, policies in CLIENT_POLICIES.items()
            for priority, (first, resolution) in policies.items()
        ]
    )

    categories = Category.objects.bulk_create(
        [Category(name=name, is_active=active) for name, active in CATEGORIES]
    )

    staff = User.objects.bulk_create(
        [
            User(
                email=f"{local}@{STAFF_DOMAIN}",
                full_name=name,
                role=role,
                is_active=active,
                # The demo admin also gets the Django admin (calendars, categories...).
                is_staff=role == Role.ADMIN,
                is_superuser=role == Role.ADMIN,
                password=password_hash,
            )
            for local, name, role, active in STAFF
        ]
    )
    clients = {}
    for slug, people in CLIENTS.items():
        organization = by_slug[slug]
        clients[organization.pk] = User.objects.bulk_create(
            [
                User(
                    email=f"{local}@{slug}.example",
                    full_name=name,
                    role=Role.CLIENT,
                    organization=organization,
                    is_active=active,
                    password=password_hash,
                )
                for local, name, active in people
            ]
        )

    return Catalog(
        organizations=organizations,
        categories=categories,
        admin=next(user for user in staff if user.role == Role.ADMIN),
        agents=[user for user in staff if user.role == Role.AGENT],
        clients=clients,
    )


def _create_calendar(spec: dict, years: range) -> BusinessCalendar:
    calendar = BusinessCalendar.objects.create(name=spec["name"], timezone=spec["timezone"])
    BusinessHours.objects.bulk_create(
        [
            BusinessHours(calendar=calendar, weekday=day, start_time=start, end_time=end)
            for day, start, end in spec["hours"]
        ]
    )
    Holiday.objects.bulk_create(
        [
            Holiday(calendar=calendar, date=holiday_date(rule, year), name=name)
            for year in years
            for rule, name in spec["holidays"]
        ]
    )
    return calendar
