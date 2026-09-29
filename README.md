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

## Setup: getting your tokens

poschodoch.sk signs you in via Google in the browser, and there's no
practical way for Home Assistant to replay that flow itself. Instead,
a one-time manual step hands the integration two paired credentials:

1. Open <https://www.poschodoch.sk> in a desktop browser and sign in.
2. Open DevTools (F12) → **Application** tab → **Local Storage** →
   `https://www.poschodoch.sk`.
3. Copy the values of **both** `id_token` and `id_refresh_token`.
4. In Home Assistant, add the integration and paste both values in —
   they're required together, since renewing a session means
   presenting the current access token (`id_token`) alongside its
   refresh token (`id_refresh_token`).

**Important:** copy both values *immediately before* completing setup
and don't reuse old/noted-down values. `id_refresh_token` is a
single-use, rotating credential — each time it's exchanged for a new
session, a new refresh token is issued and the old one stops working.
It's fine if `id_token` looks "expired" by the time you paste it (that
token being expired is exactly why a refresh happens); what matters is
that the *pair* is the most recent one your browser has, not that
either value is individually still valid.

If the integration ever shows as needing re-authentication (the token
pair was rejected/revoked), repeat these same steps with a fresh pair.

## Sensors

| Sensor | What it shows |
|---|---|
| Cold water status | % vs. last year's consumption to date |
| Hot water status | % vs. last year's consumption to date |
| Heating status | % vs. last year's consumption to date |
| Cold water daily | Most recent day's cold water use (L) |
| Hot water daily | Most recent day's hot water use (L) |
| Heating — `<room>` | One sensor per room's heat-cost allocator, created automatically from your building's actual meters |
| Account balance | Current balance due/credit (EUR) |
| Repair fund balance | Current year's repair fund balance (EUR) |

Data refreshes hourly.

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install aiohttp aioresponses pytest pytest-asyncio pytest-homeassistant-custom-component

pytest tests/
```
