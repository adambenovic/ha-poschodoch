import asyncio

import aiohttp
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(scope="session", autouse=True)
def _warm_up_aiohttp():
    """aiohttp spawns a helper thread on the first-ever ClientSession in a
    process. Absorb that one-time cost here so it doesn't get flagged as a
    per-test thread leak by pytest-homeassistant-custom-component."""

    async def _warm_up():
        async with aiohttp.ClientSession():
            pass

    asyncio.run(_warm_up())
