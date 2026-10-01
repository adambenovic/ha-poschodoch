"""Constants for the poschodoch.sk integration."""

DOMAIN = "poschodoch"

CONF_ID_TOKEN = "id_token"
CONF_ID_REFRESH_TOKEN = "id_refresh_token"
CONF_TOKEN_EXPIRES_AT = "token_expires_at"
CONF_REFRESH_AFTER = "refresh_after"
CONF_STATS_BACKFILLED = "stats_backfilled"
# Only present on entries set up via email+password login. Their absence
# is what gates whether PoschodochApiClient attempts a silent self-heal
# login when the refresh token is rejected.
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_DEVICE_COOKIE = "device_cookie"

# Polling is anchored to a fixed local time rather than a fixed interval
# (poschodoch.sk's data is daily-granularity anyway, and there's no
# benefit to hammering it hourly) — see async_track_time_change in
# __init__.py. The one-time statistics backfill is unaffected, it's a
# separate background task.
DAILY_POLL_HOUR = 6
