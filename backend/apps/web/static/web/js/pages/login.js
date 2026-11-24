import { login, refresh } from "../api.js";

const form = document.getElementById("login-form");
const errorBox = form.querySelector("[data-form-error]");
const button = form.querySelector("button[type=submit]");
const next = safeNext(new URLSearchParams(location.search).get("next"));

// Only paths of this site. "//evil.example/" and "https://evil.example/" are
// also URLs, and following them after logging in would hand the user to another
// site that looks like this one (an open redirect).
function safeNext(value) {
  if (value && value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/\\")) {
    return value;
  }
  return "/";
}

function message(error) {
  if (error.status === 401) {
    return "Email o contraseña incorrectos.";
  }
  if (error.status === 429) {
    return "Demasiados intentos. Espera un minuto y vuelve a probar.";
  }
  return "No se ha podido iniciar sesión. Inténtalo de nuevo.";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  button.disabled = true;
  try {
    await login(form.elements.email.value.trim(), form.elements.password.value);
    location.assign(next);
  } catch (error) {
    errorBox.textContent = message(error);
    errorBox.hidden = false;
    button.disabled = false;
  }
});

// Still signed in (the cookie lasts a day): no need to type the password again.
try {
  if (await refresh()) {
    location.replace(next);
  }
} catch {
  // The server could not be reached: the form stays, and its errors will say so.
}
