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

## Setup: getting your refresh token

poschodoch.sk signs you in via Google in the browser, and there's no
practical way for Home Assistant to replay that flow itself. Instead,
a one-time manual step hands the integration a long-lived credential:

1. Open <https://www.poschodoch.sk> in a desktop browser and sign in.
2. Open DevTools (F12) → **Application** tab → **Local Storage** →
   `https://www.poschodoch.sk`.
3. Find the key `id_refresh_token` and copy its value.
4. In Home Assistant, add the integration and paste that value in.

If the integration ever shows as needing re-authentication (the
refresh token was rejected/revoked), repeat these same steps.

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

## Known limitation

The exact response shape of poschodoch.sk's token-refresh endpoint
(`Auth/Refresh`) was reverse-engineered through safe, credential-free
probing (its request format is confirmed) but its *success* response
shape was never observed against a real account, since that would
have required testing with a genuine, live refresh token against
someone's production session. The parsing for that one response is
written defensively and isolated to a single method
(`PoschodochApiClient._refresh`) in case it needs a small adjustment
the first time it runs against a real token — if setup fails
unexpectedly, that's the first place to look.

## Development

```bash
python3 -m venv venv
source venv/bin/activate
pip install aiohttp aioresponses pytest pytest-asyncio pytest-homeassistant-custom-component

pytest tests/
```
