# ADR-0006: Browser tokens: access in memory, refresh in an HttpOnly cookie

- Status: Accepted
- Date: 2026-11-24
- Extends: [ADR-0004](0004-rls-request-context.md), which defines the JWT tokens and how
  each request gets its row-level security context.

## Context

The web frontend (phase 9) is a set of pages served by Django whose JavaScript calls
the API. The API authenticates with `Authorization: Bearer <access>`: a 15-minute
access token, renewed with a 1-day refresh token (ADR-0004). The browser has to keep
both somewhere, and where decides what an attacker gets from a cross-site scripting
(XSS) flaw or a cross-site request (CSRF):

| Option | XSS | CSRF |
|---|---|---|
| Both tokens in `localStorage` | Any injected script reads them and sends them away: a day of access from anywhere | None: tokens are not sent automatically |
| Django sessions for the web (cookie + `SessionAuthentication`) | The cookie cannot be read | Needs CSRF tokens; and a second way to set the RLS context next to the JWT one |
| **Access in memory, refresh in an `HttpOnly` cookie** | The script can call the API while the page is open, but cannot carry the refresh token away | The cookie is `SameSite=Strict` and only sent to the refresh endpoints |

## Decision

**Access token in memory.** The page's JavaScript keeps it in a variable and sends it
as `Authorization: Bearer`, like any API client. It is lost on reload; nothing about it
is stored in the browser.

**Refresh token in a cookie**, set and read only by three endpoints for the browser,
under `/api/auth/session/`:

- `POST /api/auth/session/` with email and password: the same login (throttle
  included) as `POST /api/auth/token/`; returns the access token in the body and sets
  the refresh token in the cookie.
- `POST /api/auth/session/refresh/`: reads the cookie and returns a new access token.
  The page calls it on load, and again when a request gets a `401`.
- `POST /api/auth/session/logout/`: removes the cookie.

The cookie is `HttpOnly` (scripts cannot read it), `SameSite=Strict` (never sent with a
request started from another site), `Path=/api/auth/session/` (never sent anywhere
else), `Secure` outside development, and lives as long as the refresh token.

**CSRF.** `SameSite=Strict` already keeps the cookie out of cross-site requests. As a
second barrier, the three endpoints only accept `application/json`: an HTML form from
another site can only send form or plain-text bodies, and JSON from another origin
needs a CORS preflight the API never grants. The same rule stops login CSRF (a site
logging the victim into the attacker's account).

**Unchanged.** `POST /api/auth/token/` and `/refresh/` keep returning both tokens in the
body for scripts and other API clients. Still no rotation and no blacklist (ADR-0004):
every request reads the user's role and organization from the database and rejects a
deactivated user, which is the revocation that matters here.

## Consequences

- An XSS flaw still lets the injected script use the session while the page is open.
  Two measures limit that: a Content-Security-Policy that allows only the site's own
  scripts, and rendering user data with `textContent`, never `innerHTML`.
- Logging out removes the cookie, but a copied refresh token would stay valid until it
  expires (one day), as with any token without a blacklist. Acceptable here; a
  blacklist (simplejwt's app) is the step if it stops being.
- A reload costs one extra request (the refresh) before the page can load its data.
- Tabs share the cookie, so a new tab is already logged in; each one keeps its own
  access token in memory.
