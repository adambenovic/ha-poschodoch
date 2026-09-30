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
| `GET Object/RepairFund?menuId=<id>&year=<yyyy>` | Building repair-fund ledger | `RepairFund[]` (`Amount`, `TvorbaCerpanie`, `Date`, `Description`) for the requested year's transactions, plus top-level `FinalBalance` (authoritative running balance as of now — **do not** re-derive by summing `RepairFund[]`, see below), `OpeningBalance`, `YearFrom`/`YearTo` (the fund's full history range), and monthly `Balance01`..`Balance12` |

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
| Repair fund balance | the response's own top-level `FinalBalance` field (a single call for the current year) | `recent_entries` (current year's, last 5, sorted by date desc), `year`, `since_year` (from `YearFrom`) |

**Two false starts, corrected through live testing:**
1. First attempt summed `Amount` across only the current calendar year's ledger — wrong by tens of thousands of euros, since the fund accumulates over many years, not one.
2. Second attempt tried to reconstruct the lifetime balance by walking backward year-by-year and summing every year's `Amount`. This actually made things *worse*: the ledger mixes in loan disbursement/repayment pairs (`uver`/`Splatka uveru`) that mostly, but not exactly, net to zero, so re-deriving a balance from raw transactions doesn't reproduce the real number even with fully correct, non-duplicated year data. It also ran into a genuine backend quirk — `Object/RepairFund?year=<yyyy>` does **not** return an empty list once you walk far enough back past the fund's actual start; it silently ignores the `year` param and echoes back the current year's ledger instead, which without detection caused the same entries to be counted many times over.

The actual fix: the response already carries the answer directly. `FinalBalance` is the server's own authoritative running balance (matches the real website exactly), and `YearFrom`/`YearTo` on the very same response state the fund's full history range with no probing needed. No client-side summation across transactions is required or correct for this value — same pattern as `Flat/Account`'s `DueBalance`.

Per-room heating sensors are created dynamically from whatever rooms
`MeterReadings` returns for meter type `UK` — no hardcoded room names,
so this adapts automatically to any building's actual layout.

## Long-term statistics (`statistics.py`)

The daily water/heating sensors only ever show the latest reading, but
`Flat/DailyConsumption` has years of real history available — confirmed
live via `year`/`month` query params (e.g. cold water data goes back to
at least 2010; heating starts later, at whatever point each room's meter
was added). This is imported into HA's long-term statistics store
(Settings → Statistics, Energy dashboard) as a standalone addition —
separate statistic IDs (`poschodoch:cold_water_daily`,
`poschodoch:hot_water_daily`, `poschodoch:heating_daily_<room slug>`),
no new entities, existing sensors untouched.

- **One-time backfill**: on first setup only (guarded by a
  `stats_backfilled` flag persisted on the config entry), walks backward
  month by month calling `Flat/DailyConsumption?...&year=<yyyy>&month=<mm>`
  for both the water (`type=S`, yields cold+hot together) and heating
  (`type=U`, yields all rooms together) series, until a month no longer
  counts as a "hit". Runs as an entry-scoped background task so it never
  blocks or delays startup even if it takes minutes to walk years of
  history.
- **What actually counts as a "hit" (several false starts, corrected
  live)**: a date match alone isn't enough. For sufficiently old
  periods the API returns *correctly-dated placeholder entries with
  `Consumption` always null* — the date scaffolding exists for years
  before any usable metered data does. An earlier version only checked
  the date, which walked the sweep through decades of real-looking but
  entirely unusable null months (300+ calls) before the "any date
  matches" condition finally failed — and even then, `_build_statistics`
  silently filtered every null-consumption entry back out, so all that
  extra walking produced no more usable data than a much shorter sweep
  would have. A month now only counts as a hit if at least one entry
  has both a matching date *and* non-null consumption, so the sweep
  stops at the true edge of usable history, not the edge of the date
  scaffolding underneath it. Also required tolerating a short run of
  misses (not stopping at the very first one — an isolated bad/empty
  response was observed mid-history) and tolerating per-month fetch
  failures (a sufficiently old month can return a non-JSON body,
  confirmed live) as misses rather than letting either abort the whole
  sweep and lose everything already found.
- **A debugging trap worth naming**: mid-investigation, `poschodoch:*`
  statistic IDs with no `unit_of_measurement` (the heating series)
  appeared to have zero data via `recorder/statistics_during_period`
  with a bucketed `period`, even once the real bug above was fixed and
  the data was genuinely written. Querying the exact same statistic_id
  via `get_last_statistics` (unbucketed) showed real data all along —
  the bucketed period-query path, not the integration, was the thing
  misbehaving for unitless statistics. Lesson: when a live check
  disagrees with the code's own logic and tests, verify via the
  simplest possible read path before concluding the write is broken.
- **Ongoing sync**: every regular coordinator poll re-uses the
  current-period data it already fetched (no extra API calls) and
  imports any days not yet present in long-term stats, continuing the
  cumulative `sum` from the last known point (via
  `get_last_statistics`). A poll that misses several days across a
  month boundary won't backfill the gap — that's an accepted, self-
  healing-on-restart limitation, not attempted here.
- Both the backfill and the per-poll sync call
  `async_add_external_statistics`, which internally queues an idempotent
  import job — safe to call with overlapping timestamps from both paths
  without special coordination between them.
- A failure in either path is caught and logged as a warning at the call
  site (`__init__.py` for backfill, `coordinator.py` for the per-poll
  sync) — long-term statistics are supplementary and must never affect
  the integration's own loaded state or the live sensors.

### Rolling 30-day averages

Exposed as an `average_last_30_days` attribute on the existing daily
water/heating sensors (not new entities) — computed every poll via
`get_rolling_average()`/`get_rolling_averages()` in `statistics.py` and
stored in `coordinator.data["rolling_averages"]`, keyed the same way as
`daily_consumption`/`heating_daily_consumption` ("S"/"T" for water, room
name for heating).

**Why not `stat_type: "mean"`:** the recorder's `mean`/`max`/`min`
aggregates are computed from per-point `mean`/`max`/`min` *columns*,
which this integration never populates (only `state` and `sum`, since
`mean_type=NONE`/`has_mean=False` was set deliberately — this is a
cumulative-sum series, not a sampled one). Confirmed via the recorder's
own source: querying "mean" for a has_sum-only series just reads back
null. `change` (the built-in period-total computation: newest sum minus
oldest sum in the window) divided by the window length in days is the
correct way to get a genuine average-per-day figure — this is the same
computation the "statistic" dashboard card uses via
`recorder/statistic_during_period`, just done in Python
(`statistic_during_period`, singular) via
`hass.async_add_executor_job` since it's the same kind of blocking DB
call as `get_last_statistics`. Same resilience contract as the
statistics sync: a failure is caught in the coordinator and falls back
to `{}`, never breaking the live sensors.

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
