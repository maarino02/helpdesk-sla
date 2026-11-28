import { api } from "../api.js";
import { addOptions, badge, el } from "../dom.js";
import { PRIORITY, SLA_STATE, STATUS, formatDateTime } from "../labels.js";
import { showError, startPage } from "../layout.js";

const FILTERS = ["status", "priority", "assignee"];

const user = await startPage();
const staff = user.role !== "client";
const form = document.getElementById("filters");
const rows = document.getElementById("ticket-rows");
const empty = document.getElementById("empty");
const more = document.getElementById("more");

// The URL of the next page, as the API gives it (a cursor), or null at the end.
let next = null;
// Each load gets a number; an answer for an older one is dropped. Without it, a
// slow answer to the previous filters could arrive last and mix in its rows.
let generation = 0;

addOptions(form.elements.status, STATUS);
addOptions(form.elements.priority, PRIORITY);
if (staff) {
  for (const member of await api("/api/staff/")) {
    form.elements.assignee.append(el("option", { value: String(member.id), text: member.full_name }));
  }
}

// The filters live in the URL: reloading, going back or sharing the link keeps
// them. A value that is not one of the options is left out by the select itself.
const fromUrl = new URLSearchParams(location.search);
for (const name of FILTERS) {
  if (fromUrl.has(name)) {
    form.elements[name].value = fromUrl.get(name);
  }
}

function query() {
  const params = new URLSearchParams();
  for (const name of FILTERS) {
    const value = form.elements[name].value;
    if (value) {
      params.set(name, value);
    }
  }
  return params;
}

function row(ticket) {
  const cells = [
    el("td", {}, el("a", { href: `/tickets/${ticket.id}/`, text: ticket.reference })),
    el("td", { className: "title", text: ticket.title }),
    el("td", {}, badge("status", ticket.status, STATUS)),
    el("td", {}, badge("priority", ticket.priority, PRIORITY)),
    el("td", {}, badge("sla", ticket.sla_state, SLA_STATE)),
  ];
  if (staff) {
    cells.push(
      el("td", { className: "name", text: ticket.organization.name }),
      el("td", { className: "name", text: ticket.assignee?.full_name ?? "Sin asignar" }),
    );
  }
  cells.push(el("td", { className: "date", text: formatDateTime(ticket.created_at) }));
  return el("tr", {}, ...cells);
}

async function showPage(url, current) {
  more.disabled = true;
  try {
    const page = await api(url);
    if (current !== generation) {
      return;
    }
    rows.append(...page.results.map(row));
    next = page.next;
  } catch {
    if (current !== generation) {
      return;
    }
    showError("No se han podido cargar los tickets. Recarga la página para intentarlo de nuevo.");
    next = null;
  }
  empty.hidden = rows.children.length > 0;
  more.hidden = next === null;
  more.disabled = false;
}

function load() {
  const params = query();
  history.replaceState(null, "", params.size ? `?${params}` : location.pathname);
  rows.replaceChildren();
  generation += 1;
  return showPage(`/api/tickets/?${params}`, generation);
}

form.addEventListener("change", load);
more.addEventListener("click", () => showPage(next, generation));
await load();
