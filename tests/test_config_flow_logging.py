import logging
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries

from custom_components.poschodoch.const import DOMAIN


@pytest.mark.asyncio
async def test_unexpected_exception_is_logged(hass, caplog):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        with caplog.at_level(logging.ERROR):
            await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {"id_token": "some-id-token", "id_refresh_token": "some-refresh-token"},
            )

    assert "boom" in caplog.text
