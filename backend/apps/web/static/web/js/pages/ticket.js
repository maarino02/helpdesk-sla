import { ApiError, api } from "../api.js";
import { addOptions, badge, el } from "../dom.js";
import { PRIORITY, SLA_STATE, STATUS, formatDateTime } from "../labels.js";
import { showError, startPage } from "../layout.js";

// What each button of a transition says: the same target reads differently
// depending on where the ticket is. Every pair of the state machine has one
// (apps/web/tests/test_pages.py).
const TRANSITION_LABELS = {
  "new>open": "Atender",
  "open>pending": "Esperar al cliente",
  "pending>open": "Reanudar",
  "open>resolved": "Resolver",
  "pending>resolved": "Resolver",
  "resolved>open": "Reabrir",
  "resolved>closed": "Cerrar",
};

const root = document.getElementById("ticket");
const id = root.dataset.ticketId;
const TICKET_URL = `/api/tickets/${id}/`;

const user = await startPage();
const staff = user.role !== "client";
const triageForm = document.getElementById("triage-form");
const commentForm = document.getElementById("comment-form");
let ticket = null;

function field(name) {
  return root.querySelector(`[data-field="${name}"]`);
}

function showMessage(selector, message) {
  const box = root.querySelector(selector);
  box.textContent = message;
  box.hidden = !message;
}

// Every page of a list that the API paginates with a cursor (comments, history).
async function all(url) {
  const items = [];
  while (url) {
    const page = await api(url);
    items.push(...page.results);
    url = page.next;
  }
  return items;
}

function fact(term, value) {
  return [el("dt", { text: term }), el("dd", { text: value || "—" })];
}

function deadline(due, done) {
  const parts = [`Plazo: ${formatDateTime(due)}`];
  if (done) {
    parts.push(`hecho: ${formatDateTime(done)}`);
  }
  return parts.join(" · ");
}

function renderTicket() {
  document.title = `${ticket.reference} · HelpDesk SLA`;
  field("reference").textContent = ticket.reference;
  field("title").textContent = ticket.title;
  field("description").textContent = ticket.description;
  root.querySelector("[data-badges]").replaceChildren(
    badge("status", ticket.status, STATUS),
    badge("priority", ticket.priority, PRIORITY),
    badge("sla", ticket.sla_state, SLA_STATE),
  );
  root.querySelector("[data-facts]").replaceChildren(
    ...fact("Cliente", ticket.organization.name),
    ...fact("Solicitante", ticket.requester.full_name),
    ...fact("Asignado", ticket.assignee?.full_name ?? "Sin asignar"),
    ...fact("Categoría", ticket.category.name),
    ...fact("Creado", formatDateTime(ticket.created_at)),
    ...fact("Primera respuesta", deadline(ticket.first_response_due_at, ticket.first_response_at)),
    ...fact("Resolución", deadline(ticket.resolution_due_at, ticket.resolved_at)),
    ...(ticket.closed_at ? fact("Cerrado", formatDateTime(ticket.closed_at)) : []),
  );

  const buttons = ticket.allowed_transitions.map((to) =>
    el("button", {
      type: "button",
      className: "button secondary",
      "data-to": to,
      text: TRANSITION_LABELS[`${ticket.status}>${to}`] ?? STATUS[to],
    }),
  );
  root.querySelector("[data-transitions]").replaceChildren(...buttons);
  root.querySelector("[data-no-transitions]").hidden = buttons.length > 0;

  const closed = ticket.status === "closed";
  commentForm.hidden = closed; // The API refuses comments on closed tickets (409).
  if (staff) {
    triageForm.closest("section").hidden = closed;
    triageForm.elements.assignee.value = ticket.assignee ? String(ticket.assignee.id) : "";
    triageForm.elements.priority.value = ticket.priority;
    // The priority is locked once resolved (409 priority_locked).
    triageForm.elements.priority.disabled = ticket.status === "resolved";
    const category = triageForm.elements.category;
    if (![...category.options].some((option) => option.value === String(ticket.category.id))) {
      // An inactive category is not in /api/categories/, but it is still this ticket's.
      category.append(el("option", { value: String(ticket.category.id), text: ticket.category.name }));
    }
    category.value = String(ticket.category.id);
  }
}

function renderComments(comments) {
  const items = comments.map((comment) =>
    el(
      "li",
      { className: comment.is_internal ? "comment internal" : "comment" },
      el(
        "p",
        { className: "comment-meta" },
        el("strong", { text: comment.author.full_name }),
        ` · ${formatDateTime(comment.created_at)}`,
        comment.is_internal ? el("span", { className: "badge", text: "Nota interna" }) : null,
      ),
      el("p", { className: "comment-body", text: comment.body }),
    ),
  );
  root.querySelector("[data-comments]").replaceChildren(...items);
  root.querySelector("[data-no-comments]").hidden = items.length > 0;
}

function describe(event) {
  const from = event.from_display ?? "—";
  const to = event.to_display ?? "—";
  switch (event.event_type) {
    case "created":
      return "Creó el ticket";
    case "status_changed":
      return `Estado: ${from} → ${to}`;
    case "assignee_changed":
      return event.to_value ? `Asignado a ${to}` : `Quitó la asignación a ${from}`;
    case "priority_changed":
      return `Prioridad: ${from} → ${to}`;
    case "category_changed":
      return `Categoría: ${from} → ${to}`;
    case "sla_recalculated":
      return "Plazos recalculados por un cambio de calendario";
    default:
      return event.event_type;
  }
}

function renderEvents(events) {
  const items = events.map((event) =>
    el(
      "li",
      {},
      el("span", { className: "date", text: formatDateTime(event.created_at) }),
      el("strong", { text: event.actor?.full_name ?? "Sistema" }),
      el("span", { text: describe(event) }),
    ),
  );
  root.querySelector("[data-events]").replaceChildren(...items);
}

// After any change: a comment can move the ticket (a first response opens it, a
// client reply resumes it), so everything is read again.
async function reload() {
  const [fresh, comments, events] = await Promise.all([
    api(TICKET_URL),
    all(`${TICKET_URL}comments/`),
    all(`${TICKET_URL}events/`),
  ]);
  ticket = fresh;
  renderTicket();
  renderComments(comments);
  renderEvents(events);
}

async function prepareTriage() {
  for (const member of await api("/api/staff/")) {
    triageForm.elements.assignee.append(
      el("option", { value: String(member.id), text: member.full_name }),
    );
  }
  addOptions(triageForm.elements.priority, PRIORITY);
  // In the API's order; see ticket_new.js on why not through an object.
  for (const category of await api("/api/categories/")) {
    triageForm.elements.category.append(
      el("option", { value: String(category.id), text: category.name }),
    );
  }
}

// The message for the user. A 403 or 409 explains itself in detail, a 400 lists
// its problems by field; a network failure has no Spanish message of its own.
function explain(error) {
  if (!(error instanceof ApiError)) {
    return "No se ha podido conectar con el servidor. Inténtalo de nuevo.";
  }
  if (error.status === 400) {
    const messages = Object.values(error.body).flat().filter((m) => typeof m === "string");
    if (messages.length) {
      return messages.join(" ");
    }
  }
  return error.body.detail ?? "No se ha podido guardar. Inténtalo de nuevo.";
}

// A 409 also means the ticket changed under us (someone else moved it): it is
// read again, so the page shows what the message talks about.
async function failed(selector, error) {
  showMessage(selector, explain(error));
  if (error.status === 409) {
    await reload();
  }
}

root.querySelector("[data-transitions]").addEventListener("click", async (event) => {
  const button = event.target.closest("button[data-to]");
  if (!button) {
    return;
  }
  showMessage("[data-action-error]", "");
  button.disabled = true;
  try {
    await api(`${TICKET_URL}transitions/`, { method: "POST", body: { to_status: button.dataset.to } });
    await reload();
  } catch (error) {
    await failed("[data-action-error]", error);
  } finally {
    button.disabled = false;
  }
});

triageForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage("[data-triage-error]", "");
  // Only what changed: each change is an event in the history, and sending the
  // current value of a field records nothing anyway.
  const elements = triageForm.elements;
  const changes = {};
  const assignee = elements.assignee.value ? Number(elements.assignee.value) : null;
  if (assignee !== (ticket.assignee?.id ?? null)) {
    changes.assignee = assignee;
  }
  if (!elements.priority.disabled && elements.priority.value !== ticket.priority) {
    changes.priority = elements.priority.value;
  }
  if (Number(elements.category.value) !== ticket.category.id) {
    changes.category = Number(elements.category.value);
  }
  if (Object.keys(changes).length === 0) {
    return;
  }
  const button = triageForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(TICKET_URL, { method: "PATCH", body: changes });
    await reload();
  } catch (error) {
    await failed("[data-triage-error]", error);
  } finally {
    button.disabled = false;
  }
});

commentForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  showMessage("[data-comment-error]", "");
  const body = commentForm.elements.body.value.trim();
  if (!body) {
    showMessage("[data-comment-error]", "Escribe un comentario.");
    return;
  }
  const button = commentForm.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    await api(`${TICKET_URL}comments/`, {
      method: "POST",
      body: { body, is_internal: staff && commentForm.elements.is_internal.checked },
    });
    commentForm.reset();
    await reload();
  } catch (error) {
    await failed("[data-comment-error]", error);
  } finally {
    button.disabled = false;
  }
});

try {
  if (staff) {
    await prepareTriage();
  }
  await reload();
  root.querySelector("[data-loaded]").hidden = false;
} catch (error) {
  if (error.status === 404) {
    root.querySelector("[data-not-found]").hidden = false;
  } else {
    showError("No se ha podido cargar el ticket. Recarga la página para intentarlo de nuevo.");
  }
}
