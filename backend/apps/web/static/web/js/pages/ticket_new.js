import { api } from "../api.js";
import { addOptions, el } from "../dom.js";
import { PRIORITY } from "../labels.js";
import { showError, startPage } from "../layout.js";

const FIELDS = ["title", "description", "category", "priority"];

const user = await startPage();
const form = document.getElementById("ticket-form");
const formError = form.querySelector("[data-form-error]");
const button = form.querySelector("button[type=submit]");

function clearErrors() {
  formError.hidden = true;
  for (const box of form.querySelectorAll("[data-error-for]")) {
    box.hidden = true;
  }
}

// A 400 lists the problems by field ({"title": ["..."]}); anything else is one
// message for the form.
function showErrors(error) {
  let shown = false;
  for (const field of FIELDS) {
    const messages = error.body?.[field];
    if (Array.isArray(messages)) {
      const box = form.querySelector(`[data-error-for="${field}"]`);
      box.textContent = messages.join(" ");
      box.hidden = false;
      shown = true;
    }
  }
  if (!shown) {
    formError.textContent =
      error.status === 400 && error.body?.non_field_errors
        ? error.body.non_field_errors.join(" ")
        : "No se ha podido crear el ticket. Inténtalo de nuevo.";
    formError.hidden = false;
  }
}

async function prepare() {
  // In the API's order (by name). Not through an object keyed by id: JavaScript
  // orders integer keys numerically, which would sort them by id instead.
  for (const category of await api("/api/categories/")) {
    form.elements.category.append(el("option", { value: String(category.id), text: category.name }));
  }
  addOptions(form.elements.priority, PRIORITY);
  form.elements.priority.value = "medium";
}

// Only clients see the form (layout.js hides it for staff, who get a notice).
if (user.role === "client") {
  try {
    await prepare();
  } catch {
    showError("No se han podido cargar las categorías. Recarga la página para intentarlo de nuevo.");
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearErrors();
    button.disabled = true;
    const data = Object.fromEntries(FIELDS.map((field) => [field, form.elements[field].value]));
    data.title = data.title.trim();
    try {
      const ticket = await api("/api/tickets/", { method: "POST", body: data });
      location.assign(`/tickets/${ticket.id}/`);
    } catch (error) {
      showErrors(error);
      button.disabled = false;
    }
  });
}
