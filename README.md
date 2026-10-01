# poschodoch.sk for Home Assistant

Custom integration that brings data from the Slovak property-management
portal [poschodoch.sk](https://www.poschodoch.sk) (ANASOFT's Domus
platform) into Home Assistant: water consumption (cold/hot), heating
(radiator heat-cost allocators), account balance/payments, and repair
fund balance.

## Installation

### Via HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories → add this repository
   URL, category "Integration".
2. Install "poschodoch.sk", restart Home Assistant.
3. Settings → Devices & Services → Add Integration → search
   "poschodoch.sk".

### Manual

Copy `custom_components/poschodoch` into your Home Assistant
`custom_components` folder and restart.

## Setup: signing in

Adding the integration (or reauthenticating later) offers a choice of
two login methods.

### Email + password (recommended)

The same login as the poschodoch.sk website. An unrecognized device is
asked for a one-time code emailed to you; once a device is recognized,
future logins (including the automatic recovery described below) skip
that step.

Your email and password are stored in Home Assistant's local
configuration (same storage, same trust model as the token pair the
manual method below stores) so that re-authentication can happen
automatically.

### Paste tokens manually

poschodoch.sk also signs you in via Google in the browser, and there's
no practical way for Home Assistant to replay *that* flow itself.
Instead, a one-time manual step hands the integration two paired
credentials:

1. Open <https://www.poschodoch.sk> in a desktop browser and sign in.
2. Open DevTools (F12) → **Application** tab → **Local Storage** →
   `https://www.poschodoch.sk`.
3. Copy the values of **both** `id_token` and `id_refresh_token`.
4. In Home Assistant, add the integration, choose "Paste tokens
   manually", and paste both values in — they're required together,
   since renewing a session means presenting the current access token
   (`id_token`) alongside its refresh token (`id_refresh_token`).

**Important:** copy both values *immediately before* completing setup
and don't reuse old/noted-down values. `id_refresh_token` is a
single-use, rotating credential — each time it's exchanged for a new
session, a new refresh token is issued and the old one stops working.
It's fine if `id_token` looks "expired" by the time you paste it (that
token being expired is exactly why a refresh happens); what matters is
that the *pair* is the most recent one your browser has, not that
either value is individually still valid.

### If the session expires

poschodoch.sk can reject the stored session at any time (observed
live: a refresh token can be rejected well before its nominal 2-hour
lifetime would suggest, for reasons outside this integration's
control). What happens next depends on which login method you used:

- **Email + password:** the integration logs back in automatically
  using the saved credentials. If your device is still recognized, this
  is silent — you won't see anything. You'll only be asked to
  reauthenticate if the device trust itself has lapsed and a fresh
  emailed code is required (nothing can complete that automatically,
  since it needs a human to read the email).
- **Manual tokens:** always requires reauthentication — repeat the
  DevTools steps above with a fresh pair (or switch to email+password
  during reauth to get the automatic recovery going forward).

## Sensors

| Sensor | What it shows | Notable attributes |
|---|---|---|
| Cold water status | % vs. last year's consumption to date | `actual_consumption`, `diff_consumption` |
| Hot water status | % vs. last year's consumption to date | `actual_consumption`, `diff_consumption` |
| Heating status | % vs. last year's consumption to date | `actual_consumption`, `diff_consumption` |
| Cold water daily | Most recent day's cold water use (L) | `date`, `average_last_30_days` |
| Hot water daily | Most recent day's hot water use (L) | `date`, `average_last_30_days` |
| Heating — `<room>` | One sensor per room's heat-cost allocator, created automatically from your building's actual meters | `date`, `average_last_30_days` |
| Account balance | Current balance due/credit (EUR) | `due_date`, `last_payment_amount`, `last_payment_date` |
| Repair fund balance | The fund's lifetime balance (EUR), not scoped to the current year | `since_year`, `recent_entries` |

Data refreshes once a day, at 6am local time (poschodoch.sk's own data
is daily-granularity, so polling more often than that wouldn't surface
anything new). `average_last_30_days` is a genuine trailing 30-day
daily average (see below), not a single day's reading.

If a poll fails for any reason (an expired session, a transient server
error), sensors keep showing their last known values rather than going
unavailable — they just won't have anything newer until the next
successful poll.

## Long-term statistics

Beyond the sensors above (which only ever show the latest reading),
the integration imports your full available consumption history into
Home Assistant's own long-term statistics store — the same place the
Energy dashboard and Settings → Statistics graphs read from. This
happens automatically and needs no configuration:

- **One-time backfill.** On first setup, a background task walks
  backward through your account's history (as far back as
  poschodoch.sk has real metered data, typically mid-2023 onward) and
  imports it. This can take a while on a large account since it's one
  request per month of history, but it doesn't block the integration
  from loading — sensors work immediately, the backfill just fills in
  behind them.
- **Ongoing sync.** Every daily poll imports whatever new days aren't
  in long-term statistics yet, so the history stays current without
  repeating the full backfill.

The imported statistics are separate entities from the sensors, named
`poschodoch:cold_water_daily`, `poschodoch:hot_water_daily`, and
`poschodoch:heating_daily_<room>` — pick them via "Show more" /
"statistic" card types in dashboards rather than the regular entity
picker, since they're not regular sensor entities.

### Rolling 30-day averages

Each water/heating sensor's `average_last_30_days` attribute is
computed server-side (via Home Assistant's own statistics backend) as
the trailing 30-day total divided by 30 — a genuine average-per-day
figure, not a snapshot of the average of the last 30 individual
readings. It depends on the long-term statistics above already having
enough history, so it may read as unavailable for the first day or two
after initial setup.

## Dashboard example

[`examples/dashboard.yaml`](examples/dashboard.yaml) is a full example
Lovelace view covering everything above: account/repair fund balance,
water consumption (daily/weekly/monthly graphs plus the 30-day
average), and heating consumption per room. To use it:

1. Add a new view to a dashboard, switch that view to YAML mode, and
   paste the file's contents in.
2. Update the heating room entities/statistics to match your own
   account (see the comment at the top of the file) — room names are
   account-specific, so the example uses placeholders.

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install aiohttp aioresponses pytest pytest-asyncio pytest-homeassistant-custom-component

pytest tests/
```
