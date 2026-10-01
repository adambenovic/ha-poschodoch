from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poschodoch.api import PoschodochAuthError
from custom_components.poschodoch.const import DOMAIN


@pytest.mark.asyncio
async def test_user_flow_shows_login_method_menu(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "user"
    assert set(result["menu_options"]) == {"password", "manual"}


@pytest.mark.asyncio
async def test_user_flow_creates_entry_on_valid_refresh_token(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "my-id-token", "id_refresh_token": "my-refresh-token"},
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["id_refresh_token"] == "my-refresh-token"
    assert result2["data"]["id_token"] == "my-id-token"


@pytest.mark.asyncio
async def test_user_flow_shows_error_on_invalid_refresh_token(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(side_effect=PoschodochAuthError("rejected")),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "bad-id-token", "id_refresh_token": "bad-token"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["errors"] == {"base": "invalid_auth"}


@pytest.mark.asyncio
async def test_password_flow_creates_entry_without_two_factor(hass):
    """A device already recognized by poschodoch.sk (saved cookie, not
    relevant on a brand new setup, but the server can still decide not to
    challenge) logs straight in."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    assert result["step_id"] == "password"

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(
            return_value={
                "requires_two_factor": False,
                "auth_token": "login-token",
                "refresh_token": "login-refresh-token",
                "expires_in": 7200,
                "device_cookie": "device-cookie",
            }
        ),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "hunter2"},
        )

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"]["username"] == "user@example.com"
    assert result2["data"]["password"] == "hunter2"
    assert result2["data"]["device_cookie"] == "device-cookie"
    assert result2["data"]["id_token"] == "login-token"


@pytest.mark.asyncio
async def test_password_flow_requires_two_factor_then_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(return_value={"requires_two_factor": True}),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "hunter2"},
        )

    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "two_factor"

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(
            return_value={
                "requires_two_factor": False,
                "auth_token": "login-token",
                "refresh_token": "login-refresh-token",
                "expires_in": 7200,
                "device_cookie": "device-cookie",
            }
        ),
    ) as mock_login, patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {"code": "552544"}
        )

    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"]["username"] == "user@example.com"
    assert mock_login.call_args.args[1:] == ("user@example.com", "hunter2")
    assert mock_login.call_args.kwargs == {"two_factor_code": "552544"}


@pytest.mark.asyncio
async def test_two_factor_wrong_code_retries_without_losing_credentials(hass):
    """A wrong code must re-show the two_factor step with an error,
    reusing the already-entered username/password rather than making the
    user start the whole password step over."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(return_value={"requires_two_factor": True}),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "hunter2"},
        )

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(side_effect=PoschodochAuthError("rejected")),
    ):
        result3 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], {"code": "000000"}
        )

    assert result3["type"] == FlowResultType.FORM
    assert result3["step_id"] == "two_factor"
    assert result3["errors"] == {"base": "invalid_auth"}

    # Retry with the right code, without re-entering username/password.
    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(
            return_value={
                "requires_two_factor": False,
                "auth_token": "login-token",
                "refresh_token": "login-refresh-token",
                "expires_in": 7200,
                "device_cookie": "device-cookie",
            }
        ),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ):
        result4 = await hass.config_entries.flow.async_configure(
            result3["flow_id"], {"code": "552544"}
        )

    assert result4["type"] == FlowResultType.CREATE_ENTRY
    assert result4["data"]["username"] == "user@example.com"
    assert result4["data"]["password"] == "hunter2"


@pytest.mark.asyncio
async def test_reauth_shows_login_method_menu(hass):
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
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "reauth_confirm"
    assert set(result["menu_options"]) == {"password", "manual"}


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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"

    with patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ), patch(
        "custom_components.poschodoch.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"id_token": "new-id-token", "id_refresh_token": "new-refresh-token"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["id_refresh_token"] == "new-refresh-token"
    assert entry.data["id_token"] == "new-id-token"


@pytest.mark.asyncio
async def test_reauth_via_password_updates_existing_entry(hass):
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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "password"}
    )
    assert result["step_id"] == "password"

    with patch(
        "custom_components.poschodoch.config_flow._password_login",
        new=AsyncMock(
            return_value={
                "requires_two_factor": False,
                "auth_token": "login-token",
                "refresh_token": "login-refresh-token",
                "expires_in": 7200,
                "device_cookie": "device-cookie",
            }
        ),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.activate",
        new=AsyncMock(return_value=None),
    ), patch(
        "custom_components.poschodoch.config_flow.PoschodochApiClient.get_menu_map",
        new=AsyncMock(return_value={"account": 1}),
    ), patch(
        "custom_components.poschodoch.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user@example.com", "password": "hunter2"},
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.ABORT
    assert result2["reason"] == "reauth_successful"
    assert entry.data["username"] == "user@example.com"
    assert entry.data["id_token"] == "login-token"
