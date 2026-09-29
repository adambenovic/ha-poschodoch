from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poschodoch.api import PoschodochAuthError
from custom_components.poschodoch.const import DOMAIN


@pytest.mark.asyncio
async def test_user_flow_creates_entry_on_valid_refresh_token(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "my-id-token", "refresh_token": "my-refresh-token"},
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["id_refresh_token"] == "my-refresh-token"
    assert result2["data"]["id_token"] == "my-id-token"


@pytest.mark.asyncio
async def test_user_flow_shows_error_on_invalid_refresh_token(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(side_effect=PoschodochAuthError("rejected")),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "bad-id-token", "refresh_token": "bad-token"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_reauth_updates_existing_entry_with_new_refresh_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "old-token",
            "id_refresh_token": "old-refresh-token",
            "token_expires_at": "2026-01-01T00:00:00+00:00",
            "refresh_after": "2026-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ), patch(
        "custom_components.poschodoch.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "new-id-token", "refresh_token": "new-refresh-token"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["id_refresh_token"] == "new-refresh-token"
    assert entry.data["id_token"] == "new-id-token"
