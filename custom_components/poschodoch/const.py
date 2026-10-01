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

DEFAULT_SCAN_INTERVAL_HOURS = 1
