# poschodoch.sk Home Assistant Integration — Design Spec

## Summary

A custom Home Assistant integration (`custom_components/poschodoch`) that
exposes data from the Slovak property-management portal
[poschodoch.sk](https://www.poschodoch.sk) (built on ANASOFT's Domus
platform) as HA sensors: water consumption (cold/hot), heating
(radiator heat-cost allocators), account balance/payments, and repair
fund balance. Published as a standalone git repository and a HACS
custom repository.

## Background: how poschodoch.sk works

- Frontend: a Vue SPA served from `web.poschodoch.sk` /
  `www.poschodoch.sk`.
- Backend: `https://api.poschodoch.sk/api/...`, a plain JSON REST API
  (`Anasoft.Domus.Poschodoch.WebApi`), no Cloudflare/bot protection, no
  rate-limit signals observed.
- The dashboard is **menu-driven**: `Dashboard/Menu` returns a list of
  `{MenuId, MenuCode, MenuName, ...}` entries specific to the logged-in
  user's building/unit configuration. Data endpoints are then called
  with the resolved numeric `MenuId` as a query parameter, e.g.
  `Flat/DailyConsumption?menuId=41&type=S`. **`MenuCode` is stable and
  documented below; `MenuId` is per-user/building and must be resolved
  at runtime, never hardcoded.**

### Authentication

- The user signs in via "Sign in with Google" in the browser. Google
  issues an ID token to the page's JS, which the page exchanges with
  the backend (`Auth/LoginWithGoogleToken`, confirmed to accept a JSON
  object of type `GoogleAuthViewModel`) for the app's own session:
  - `id_token` — short-lived (observed 2-hour lifetime), sent on every
    API call via the standard **`Authorization: Bearer <id_token>`**
    request header. (Earlier drafts of this spec assumed the custom
    `X-Auth-Token` header seen in the CORS `Access-Control-Allow-Headers`
    allow-list was the real auth mechanism — that allow-list only says
    what's *permitted* cross-origin, not what's *required*; live testing
    against every endpoint confirmed `Authorization: Bearer` is what's
    actually checked everywhere, including `Dashboard`/`Flat`/`Object`
    endpoints.)
  - A token from `Auth/LoginWithGoogleToken` (or `Auth/refresh`) is
    valid for authentication but is **not yet bound to a unit/portal**.
    `Auth/UnitList` works with it directly, but any `Dashboard`/`Flat`/
    `Object` call 401s until `Auth/changeunit?portalId=<id>` has been
    called with it — this activation returns its own fresh token pair,
    which is the one actually used afterward. The real SPA always calls
    `UnitList` → `changeunit` immediately after login; this integration
    now does the same, and additionally re-runs that activation after
    every `Auth/refresh` (harmless even where not strictly required,
    since the real server appears to remember unit-activation per
    account, not per token — but not guaranteed to hold in every case).
  - `Auth/refresh` rejects a `refresh_token` that has never been
    through `Auth/changeunit` — so the very first use of a pair copied
    straight out of a browser must skip `Auth/refresh` entirely and go
    directly to `UnitList` + `changeunit`.
  - `id_refresh_token` — long-lived opaque token (not a JWT), stored in
    the SPA's `localStorage`.
  - The SPA's own client policy (read directly from its localStorage
    values `token_expires_at` / `refresh_token_after`): refresh
    proactively **1 hour before** the 2-hour expiry.
- Refresh endpoint: `POST /api/Auth/Refresh`, confirmed via safe
  black-box probing (empty/garbage bodies, no real token used) to
  accept **the refresh token as a bare JSON string body** (e.g.
  `"3Sw2aW..."`, not `{"token": "..."}`) — confirmed by contrasting two
  distinct error signatures: a JSON *object* body always leaves the
  bound parameter null (`IDX10000: parameter 'token' cannot be null`),
  while a JSON *string* body binds successfully and fails later
  downstream (`NullReferenceException`) because the garbage value
  isn't a real, resolvable refresh token.
- **Known gap:** the exact JSON shape of a *successful* `Auth/Refresh`
  response has not been observed (doing so would require a real,
  valid refresh token, which was not safe/appropriate to test against
  production with fabricated data). The client implementation must be
  defensive here: parse the response for a token-like field
  permissively (see "Open risk" below) and this must be validated
  against a real account during first setup.
- The integration will **not** implement the Google login flow itself
  (browser-based Google Identity Services is impractical to reproduce
  inside a headless HA config flow). Instead, setup requires a
  **one-time manual step**: the user copies `id_refresh_token` out of
  their browser's `localStorage` (DevTools → Application → Local
  Storage → `www.poschodoch.sk`) and pastes it into the config flow.
  From then on, the integration refreshes automatically. If the
  refresh token itself is ever rejected (revoked, or backend-side
  expiry beyond what we've observed), HA's standard reauth flow
  prompts the user to repeat this same short step.

### Confirmed endpoints and payload shapes

All observed live via a real, authenticated session (HAR capture),
field names and units as returned by the API:

| Endpoint | Purpose | Key fields |
|---|---|---|
| `GET Auth/UnitList` | List of units/flats accessible to the account | `UnitId`, `PortalId`, `Address`, `Type` |
| `GET Dashboard/UnitInfo` | Basic info for the active unit | `Text1..3`, `Currency` |
| `GET Dashboard/Menu` | MenuCode → MenuId map for this user | array of `{MenuId, MenuCode, MenuName, MenuGroupCode}` |
| `GET Flat/DailyConsumption?menuId=<id>&type=S\|T\|U\|C` | Daily water/heat readings | `Consumption[]` with `Date`, `Code`, `Type`, `Consumption` (string decimal) — **the `type` query param does not cleanly filter results**: a call with `type=S` was observed returning entries for both `Code:"S"` (cold) and `Code:"T"` (hot) interleaved. The client must always partition results by reading each entry's own `Code` field, never by assuming the query param determined what came back. |
| `GET Flat/ConsumptionStatus?menuId=<id>&type=S\|T\|U` | "How am I doing vs last year" | `ActualConsumption`, `DiffConsumption`, `PercConsumption`, `Unit` |
| `GET Flat/MeterReadings?menuId=<id>&disassembled=1` | Physical meter list | `MeterId`, `MeterNumber`, `MeterType` (`SV`/`TV`/`UK`), `ClimbingIron` (room name for `UK`) |
| `GET Flat/Account?menuId=<id>` | Payment/balance ledger | `Account[]` (`Amount`, `Balance`, `Period`, `TypeOfMovement`, `DueDate`) |
| `GET Object/RepairFund?menuId=<id>&year=<yyyy>` | Building repair-fund ledger | `RepairFund[]` (`Amount`, `TvorbaCerpanie`, `Date`, `Description`) |

`type` query param groups by *domain* (water vs. heating vs. other),
not by cold/hot specifically — see the `Code`-based partitioning note
above. Within a response, `Code` values seen: `S` = cold water, `T` =
hot water, `U` = heating (per-room heat-cost allocators), `C` =
unclear/less relevant (not used in v1 sensors).

**All numeric values in every response are JSON strings** (e.g.
`"Consumption": "242.000"`, `"Amount": "-226.42"`), never native JSON
numbers. Every sensor's value must go through explicit `float()`
parsing — this is called out here so it isn't missed as an
implementation detail.

## Architecture

```
custom_components/poschodoch/
  __init__.py          # entry setup/unload, creates coordinator
  api.py                # PoschodochApiClient: HTTP + auth + refresh
  coordinator.py        # PoschodochDataUpdateCoordinator
  config_flow.py        # setup + reauth flow
  sensor.py             # sensor entity classes
  const.py              # DOMAIN, menu codes, defaults
  manifest.json
  strings.json / translations/en.json
hacs.json
README.md
tests/
  test_api.py
  test_config_flow.py
```

**`api.py` — `PoschodochApiClient`**
- Holds `id_token`, `id_refresh_token`, `token_expires_at` (computed:
  now + 2h at issuance, matching the observed lifetime), `unit_id`,
  `portal_id`.
- `async def request(method, path, **kwargs)`: injects
  `Authorization: Bearer <id_token>`; on `401`, calls `_refresh()` once
  and retries; raises `PoschodochAuthError` (mapped to
  `ConfigEntryAuthFailed` in the coordinator) if the retry also fails.
- `async def activate()`: for a token that has never been used with
  this client (e.g. straight out of the browser) — calls `UnitList` +
  `changeunit` directly with the as-given token, skipping `Auth/refresh`
  entirely (which rejects never-activated refresh tokens). Used once,
  by the config flow, during initial setup.
- `async def _refresh()`: proactive if `now >= refresh_after`,
  otherwise reactive on 401. POSTs `{"AuthToken": ..., "RefreshToken":
  ...}` (PascalCase JSON object) to `Auth/refresh` with
  `Authorization: Bearer <current id_token>`. Parses the real response
  shape: `{"auth_token": ..., "refresh_token": ..., "expires_in": ...}`
  (snake_case). The resulting token authenticates but isn't yet unit-
  bound, so `_refresh()` always finishes by re-running the same
  activation (`UnitList` + `changeunit`) before returning. Persists the
  final, activated token pair back to the config entry via
  `hass.config_entries.async_update_entry`.
- `async def get_menu_map()`: fetches and caches `Dashboard/Menu`,
  returns a `dict[MenuCode, MenuId]`. Cached for 24h (menu
  configuration changes rarely; not worth fetching every poll).
- Typed fetch methods per endpoint (`get_daily_consumption`,
  `get_consumption_status`, `get_meter_readings`, `get_account`,
  `get_repair_fund`) — each resolves its `MenuCode` to a `MenuId` via
  the cached menu map before calling. `get_daily_consumption` makes a
  single call (the water domain's `type=S` call is sufficient — see
  the `Code`-partitioning note above) and returns the raw list;
  callers partition it by `Code` into cold/hot series, so cold and hot
  water sensors share one fetch rather than two.

**`config_flow.py`**
- Single step: a form with one field, "Refresh token" (help text
  explains the DevTools steps to obtain it).
- On submit: instantiate the API client with the pasted token, force
  an immediate refresh + `Auth/UnitList` call to validate it and learn
  `unit_id`/`portal_id`; on success, create the config entry (storing
  the *current* refresh token — updated in place thereafter as it
  rotates); on failure, show a clear form error.
- `async_step_reauth`: same form, re-shown when the coordinator raises
  `ConfigEntryAuthFailed`.

**`coordinator.py`**
- `DataUpdateCoordinator` with `update_interval = timedelta(hours=1)`.
- `_async_update_data`: calls each `api.py` fetch method, assembles one
  dict keyed by sensor domain (`water_cold`, `water_hot`, `heating`,
  `account`, `repair_fund`), returns it. Any `PoschodochAuthError`
  bubbles up as `ConfigEntryAuthFailed` (triggers reauth); any other
  request failure as `UpdateFailed` (entities go `unavailable`, HA
  retries with its own backoff).

**`sensor.py`** — entities, all `CoordinatorEntity`:

| Entity | State | Key attributes |
|---|---|---|
| Cold water status | `PercConsumption` (%) | `actual_consumption`, `diff_consumption`, `unit` |
| Hot water status | same, type `T` | |
| Heating status | same, type `U` | |
| Cold water — last daily | latest day's `Consumption` (L) | `date` |
| Hot water — last daily | latest day's `Consumption` (L) | `date` |
| Heating — last daily (per room) | latest day's `Consumption`, one entity per room found in `MeterReadings` (`ClimbingIron`) | `date`, `meter_number` |
| Account balance | `DueBalance` (EUR) | `due_date`, `last_payment_amount`, `last_payment_date` |
| Repair fund balance | sum of `Amount` across every calendar year's ledger since the fund started, walked backward year-by-year (`Object/RepairFund?year=<yyyy>`) until a year's response doesn't actually belong to that year; past years are cached forever, only the current year is re-fetched each poll — a single year's sum was found to be off by thousands of euros vs. the real balance | `recent_entries` (last 5 across all years, sorted by date desc), `year`, `since_year` |

**Backend quirk (confirmed live):** `Object/RepairFund?year=<yyyy>` does **not** return an empty list once you walk far enough back past the fund's actual start — it silently ignores the `year` param and echoes back the current year's ledger instead. The walk-backward loop can't use "empty response" as its only stop condition; it must check whether the returned entries' own `Date` fields actually fall within the requested year, and stop (discarding that response) the first time they don't — otherwise the same entries get counted many times over.

Per-room heating sensors are created dynamically from whatever rooms
`MeterReadings` returns for meter type `UK` — no hardcoded room names,
so this adapts automatically to any building's actual layout.

## Error handling

- `401` on any call → one `Auth/Refresh` attempt → retry once → if
  still failing, `PoschodochAuthError`.
- `Auth/Refresh` itself rejected → `ConfigEntryAuthFailed` → HA's
  reauth flow (user repeats the manual DevTools step).
- Network/SSL errors (observed to happen at least transiently on the
  real site) → standard `UpdateFailed`, coordinator's built-in retry/
  backoff applies; entities show `unavailable` rather than a crash.

## Testing

- `tests/test_api.py`: mocked HTTP (via `aioresponses` or an injected
  fake session) covering: header injection, proactive vs. reactive
  refresh triggering, refresh-token rotation persisted correctly,
  menu-map resolution and caching, 401-retry-once-then-fail.
- `tests/test_config_flow.py`: HA's standard config-flow test harness,
  mocked API client, covering successful setup, invalid-token error
  display, and reauth flow.
- No tests run against the real production API or with real
  credentials.

## Resolved risks

Two things flagged as open/unverified in earlier drafts of this spec
were resolved through live testing against the real API once a real
account and real HAR captures were available:

- `Auth/refresh`'s success response shape: confirmed to be
  `{"auth_token": ..., "refresh_token": ..., "expires_in": ...}`.
- The actual authentication header: confirmed to be
  `Authorization: Bearer <token>` on **every** endpoint, not the
  `X-Auth-Token` header originally inferred from a CORS allow-list (an
  allow-list documents what's *permitted*, not what's *checked* server-
  side — this was a flawed inference that went undetected for a while
  because no endpoint other than `Auth/refresh` had ever been tested
  live with real credentials until debugging a real setup failure
  forced the issue).
- The unit-activation requirement (`Auth/UnitList` + `Auth/changeunit`
  needed after login *and* after every refresh) was discovered the same
  way: by reading the actual error sequence in Home Assistant's logs
  during a real failed setup, then cross-referencing against a full
  HAR of a real login to see what the genuine client does that this
  integration didn't.

## Out of scope for v1

- Building-level non-numeric info (officials/contacts, documents,
  invoices) — mentioned during scoping but not core to "everything
  numeric"; can be added later using the same coordinator/menu-map
  machinery without architecture changes.
- Multi-unit accounts (the API supports `Auth/UnitList` returning more
  than one unit; this account has exactly one). The config flow will
  simply use the first/only unit returned; extending to a picker if
  multiple units are returned is a small, isolated future addition.
- Implementing the Google login flow itself inside HA.
