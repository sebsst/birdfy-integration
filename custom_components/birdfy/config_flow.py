"""Config flow for Birdfy integration."""
from __future__ import annotations

import hashlib
import logging
import uuid

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

LOGIN_URL = "https://localweb.nvts.co/v1/users/login/v2"

UCID = "b3cf543b57"

STEP_SCHEMA = vol.Schema({
    vol.Required(CONF_EMAIL): str,
    vol.Required(CONF_PASSWORD): str,
})


def _generate_udid() -> str:
    return f"android-{uuid.uuid4()}"


async def _test_login(email: str, password: str, udid: str) -> str | None:
    """Return error key or None on success."""
    pwd_md5 = hashlib.md5(password.encode()).hexdigest()
    payload = {"username": email, "password": pwd_md5, "locale": "en-US"}
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "x-nvs-ucid": UCID,
        "x-nvs-udid": udid,
        "User-Agent": "Birdfy/1.19.2 (build 123960) NetvueSDK/1.6.1 Android/12",
    }
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post(LOGIN_URL, json=payload, headers=headers) as r:
                data = await r.json(content_type=None)
                _LOGGER.debug("Birdfy login response: %s", data)
                if data.get("ret", 0) != 0 or not data.get("token"):
                    _LOGGER.error("Birdfy login failed: %s", data)
                    return "invalid_auth"
    except Exception as e:
        _LOGGER.error("Birdfy login exception: %s", e)
        return "cannot_connect"
    return None


class BirdfyConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Birdfy config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            udid = _generate_udid()
            error = await _test_login(user_input[CONF_EMAIL], user_input[CONF_PASSWORD], udid)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(user_input[CONF_EMAIL])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_EMAIL],
                    data={
                        CONF_EMAIL: user_input[CONF_EMAIL],
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        "ucid": UCID,
                        "udid": udid,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )
