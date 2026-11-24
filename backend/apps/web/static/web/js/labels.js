// How the pages name the API's codes, and how they show dates.
//
// Kept equal to the choices in Django (Status, Priority, SlaState) by
// apps/web/tests/test_pages.py: a new value there fails the test until it has a
// label here.

export const STATUS = {
  new: "Nuevo",
  open: "Abierto",
  pending: "En espera del cliente",
  resolved: "Resuelto",
  closed: "Cerrado",
};

export const PRIORITY = {
  low: "Baja",
  medium: "Media",
  high: "Alta",
  critical: "Crítica",
};

export const SLA_STATE = {
  on_track: "En plazo",
  at_risk: "En riesgo",
  paused: "En pausa",
  breached: "Incumplido",
  met: "Cumplido",
};

// In the browser's own time zone: the API sends UTC, people read local time.
const DATE_TIME = new Intl.DateTimeFormat("es-ES", { dateStyle: "short", timeStyle: "short" });

export function formatDateTime(iso) {
  return iso ? DATE_TIME.format(new Date(iso)) : "";
}
