# Password login and self-healing reauth

## Problem

Setup today requires digging `id_token`/`id_refresh_token` out of the
browser's local storage via DevTools. Worse, when poschodoch.sk
eventually rejects the refresh token (observed live: ~15h after a
restart, cause external to this integration — see the session from
2026-10-01), the integration has no way to recover on its own and
falls back to HA's reauth flow, requiring the same manual DevTools dig
every time.

poschodoch.sk also exposes an `Auth/login` (email+password) endpoint,
confirmed via a captured HAR of a real browser login. It requires a
2FA code (emailed) only for devices it doesn't recognize yet; a
returned `cookie` value, resubmitted on a future login, is how it
recognizes a device thereafter. This gives us a path to a login method
that doesn't need the browser, and — once the device is trusted — a
non-interactive way to re-authenticate whenever the refresh chain
breaks.

## Confirmed API contract (from HAR capture)

`POST Auth/login`, body `{"UserName", "Password", "Cookie", "TwoFactorCode"}`
(`Cookie`/`TwoFactorCode` nullable):

- Unrecognized device, no code yet → `200 {"requiresTwoFactor": true}`
  (server emails a code as a side effect)
- With a valid code → `200` with the same shape `Auth/refresh` already
  returns (`auth_token`, `refresh_token`, `expires_in`), plus
  `username` and a `cookie` value to save for next time.
- `Auth/changeunit` is still required afterward, identical to today's
  flow after a manual token paste or a refresh.

No captured example of a wrong password or wrong 2FA code — both are
assumed to come back non-200, handled the same generic way the
existing `_refresh()` error path already handles rejection.

## Design

**New persisted state** (`const.py`): `CONF_USERNAME`, `CONF_PASSWORD`,
`CONF_DEVICE_COOKIE`. All absent on existing token-paste entries —
that absence is what gates whether self-heal is attempted, so no
separate "which method" flag is needed.

**`api.py`**:
- New module-level `_password_login(session, username, password,
  two_factor_code=None, device_cookie=None)`: POSTs `Auth/login`,
  returns `{"requires_two_factor": True}` or the token dict. Same
  DEBUG-only raw-body logging on rejection as `_refresh()`.
- `PoschodochApiClient` gains optional `username`/`password`/
  `device_cookie` constructor params (all `None` by default).
- `_refresh()`: on a non-200 `Auth/refresh`, if username+password are
  set, attempt `_password_login` with the saved `device_cookie` before
  giving up. Success sets fresh tokens and falls through to the
  *existing* `_activate_unit()` call unchanged. A
  `requires_two_factor` response (device trust lapsed, no human to
  answer) still raises `PoschodochAuthError` — same as today, still
  correctly surfaces as HA's reauth flow.
- `token_state` gains `device_cookie` (omitted when unset), persisted
  through the existing `on_tokens_updated` callback — no new plumbing.
- `coordinator.py` and the background backfill task need **no
  changes** — both already go through `client.request()`.

**`config_flow.py`**:
- `async_step_user` becomes `async_show_menu`: "Email + password" vs.
  "Paste tokens manually" (today's flow, renamed `async_step_manual`,
  logic unchanged).
- `async_step_password`: username+password form → `_password_login`.
  `requires_two_factor` → stash username/password on the flow
  instance, move to `async_step_two_factor` (code entry; a wrong code
  re-shows that same step with an error rather than restarting from
  username/password). Success on either step → same validation tail
  as today (`client.activate()` + `get_menu_map()`), entry data now
  also carries username/password/device_cookie.
- `async_step_reauth_confirm` gets the same menu, so reauth can use
  either method regardless of how the entry was originally set up.

## Error handling

Non-200 from `Auth/login` (bad password, bad 2FA code, locked
account, rate limit — no way to distinguish without a captured
example) → `PoschodochAuthError`, surfaces as `invalid_auth` in the
config flow, same as the existing manual path. No new error code.

## Testing

- `_password_login`: all three outcomes (2FA-required / success /
  rejected), DEBUG-only logging on rejection.
- `_refresh()`: silent self-heal success path; `PoschodochAuthError`
  when 2FA is re-required; `token_state` includes `device_cookie`.
- Config flow: menu step; password happy path; 2FA happy path; wrong
  2FA code retry preserves username/password; reauth via both paths.
- All existing manual-method tests continue to pass unchanged.

## Out of scope

- Automatically completing a 2FA challenge (no human present during a
  background poll — falls back to the existing reauth UI, unchanged).
- Migrating the live account's existing entry to password-based login
  — that's a manual choice the user makes afterward, not part of this
  change.
