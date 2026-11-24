// The API client of every page (ADR-0006).
//
// The access token lives only in this module's memory: it is gone on reload, and
// refresh() asks for a new one with the refresh cookie, which scripts cannot read.
// Imports between modules are relative and end with ";": collectstatic only
// rewrites those to the hashed file names (apps/core/storage.py).

// Kept equal to the Django URLs by apps/web/tests/test_pages.py.
export const LOGIN_PAGE = "/login/";
const SESSION = "/api/auth/session/";

let access = null;
// The refresh in flight: requests that get a 401 at the same time share it
// instead of each asking for its own token.
let refreshing = null;

export class ApiError extends Error {
  constructor(status, body) {
    super(body?.detail ?? `Error ${status}`);
    this.status = status;
    this.code = body?.code ?? null;
    // Validation errors (400) list the problems by field.
    this.body = body ?? {};
  }
}

async function send(path, { method = "GET", body, token } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  const response = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    throw new ApiError(response.status, data);
  }
  return data;
}

export async function login(email, password) {
  const data = await send(SESSION, { method: "POST", body: { email, password } });
  access = data.access;
}

// A new access token from the cookie. true if there is a session, false if not
// (no cookie, expired, or the user was deactivated); other errors are thrown.
export function refresh() {
  refreshing ??= send(`${SESSION}refresh/`, { method: "POST", body: {} })
    .then(
      (data) => {
        access = data.access;
        return true;
      },
      (error) => {
        access = null;
        if (error.status === 401) {
          return false;
        }
        throw error;
      },
    )
    .finally(() => {
      refreshing = null;
    });
  return refreshing;
}

export async function logout() {
  access = null;
  await send(`${SESSION}logout/`, { method: "POST", body: {} });
}

// Leaves for the login page, which comes back here afterwards. The returned
// promise never settles: the page is going away, and nothing after it must run.
export function goToLogin() {
  const next = encodeURIComponent(location.pathname + location.search);
  location.assign(`${LOGIN_PAGE}?next=${next}`);
  return new Promise(() => {});
}

// A request with the access token. On a 401 (the token lasts 15 minutes) it
// renews the token once and tries again; without a session, it goes to login.
export async function api(path, options = {}) {
  if (access === null && !(await refresh())) {
    return goToLogin();
  }
  try {
    return await send(path, { ...options, token: access });
  } catch (error) {
    if (error.status !== 401) {
      throw error;
    }
    if (!(await refresh())) {
      return goToLogin();
    }
    return send(path, { ...options, token: access });
  }
}
