// What every page with a session does first: check it, fill in the header and
// show the page.

import { LOGIN_PAGE, api, logout } from "./api.js";

const ROLES = { client: "Cliente", agent: "Agente", admin: "Administrador" };

export function roleName(role) {
  return ROLES[role] ?? role;
}

// The signed-in user, from /api/me/. Without a session, api() leaves for the
// login page and this never returns.
export async function startPage() {
  let user;
  try {
    user = await api("/api/me/");
  } catch {
    showError("No se ha podido conectar con el servidor. Recarga la página para intentarlo de nuevo.");
    throw new Error("No session information");
  }
  document.querySelector("[data-user-name]").textContent = user.full_name;
  document.querySelector("[data-account]").hidden = false;
  document.querySelector("[data-logout]").addEventListener("click", async () => {
    try {
      await logout();
    } finally {
      location.assign(LOGIN_PAGE);
    }
  });
  document.getElementById("main").hidden = false;
  return user;
}

// A message for the whole page, as text: never HTML.
export function showError(message) {
  const box = document.querySelector("[data-page-error]");
  box.textContent = message;
  box.hidden = false;
}
