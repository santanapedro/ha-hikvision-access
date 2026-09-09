"""Config + options flow for Hikvision Access (spec §18, §19).

User step: host / port / HTTPS / credentials -> probe the terminal
(authenticate, read deviceInfo, discover capabilities) -> confirm model and
create the entry, keyed by the terminal's serial number.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HikvisionISAPIClient
from .capabilities import async_discover
from .const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_CALL_POLL_INTERVAL_S,
    DEFAULT_EVENT_ROUTE,
    DEFAULT_HTTP_PORT,
    DEFAULT_HTTPS_PORT,
    DEFAULT_IMAGE_RETENTION_DAYS,
    DEFAULT_RECONCILE_INTERVAL_S,
    DEFAULT_REQUEST_TIMEOUT_S,
    DEFAULT_RTSP_PORT,
    DEFAULT_USE_HTTPS,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_CALL_POLL_INTERVAL_S,
    MAX_RECONCILE_INTERVAL_S,
    MIN_CALL_POLL_INTERVAL_S,
    MIN_RECONCILE_INTERVAL_S,
    OPT_ALSO_RUN_STREAM,
    OPT_CALL_POLL_INTERVAL,
    OPT_CREATE_OPEN_DOOR_BUTTON,
    OPT_ENABLE_CAMERA,
    OPT_EVENT_ROUTE,
    OPT_IMAGE_RETENTION_DAYS,
    OPT_MASK_CARD_NUMBER,
    OPT_RECONCILE_INTERVAL,
    OPT_REGISTER_PUSH_ON_DEVICE,
    OPT_REQUEST_TIMEOUT,
    OPT_RTSP_PORT,
    OPT_STORE_DENIED_IMAGES,
    OPT_STORE_GRANTED_IMAGES,
    OPT_STORE_RAW_PAYLOAD,
)
from .exceptions import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionLockoutError,
    HikvisionTimeoutError,
)

_LOGGER = logging.getLogger(__name__)


async def _probe(hass, data: dict[str, Any]) -> tuple[Any, Any]:
    session = async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL])
    client = HikvisionISAPIClient(
        session,
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        use_https=data[CONF_USE_HTTPS],
    )
    info = await client.async_get_device_info()
    caps = await async_discover(client)
    return info, caps


class HikvisionAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hikvision Access."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            use_https = user_input.get(CONF_USE_HTTPS, DEFAULT_USE_HTTPS)
            data = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_PORT: int(
                    user_input.get(
                        CONF_PORT,
                        DEFAULT_HTTPS_PORT if use_https else DEFAULT_HTTP_PORT,
                    )
                ),
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_USE_HTTPS: use_https,
                CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            }
            try:
                info, _caps = await _probe(self.hass, data)
            except HikvisionLockoutError:
                errors["base"] = "lockout"
            except HikvisionAuthError:
                errors["base"] = "invalid_auth"
            except HikvisionTimeoutError:
                errors["base"] = "timeout"
            except HikvisionConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error probing %s", data[CONF_HOST])
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(info.serial_number)
                self._abort_if_unique_id_configured(updates={CONF_HOST: data[CONF_HOST]})
                title = (
                    user_input.get(CONF_NAME, "").strip()
                    or info.device_name
                    or f"{info.model}"
                )
                return self.async_create_entry(
                    title=title,
                    data=data,
                    options=_default_options(),
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_PORT): int,
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Optional(CONF_USE_HTTPS, default=DEFAULT_USE_HTTPS): bool,
                    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                    vol.Optional(CONF_NAME): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-enter credentials for an existing terminal after auth failed."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        assert entry is not None
        errors: dict[str, str] = {}

        if user_input is not None:
            data = {
                **entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME].strip(),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                info, _caps = await _probe(self.hass, data)
            except HikvisionLockoutError:
                errors["base"] = "lockout"
            except HikvisionAuthError:
                errors["base"] = "invalid_auth"
            except HikvisionTimeoutError:
                errors["base"] = "timeout"
            except HikvisionConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error on reauth for %s", data[CONF_HOST])
                errors["base"] = "unknown"
            else:
                if entry.unique_id and info.serial_number != entry.unique_id:
                    return self.async_abort(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data=data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"host": entry.data.get(CONF_HOST, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HikvisionAccessOptionsFlow:
        return HikvisionAccessOptionsFlow()


def _default_options() -> dict[str, Any]:
    return {
        OPT_EVENT_ROUTE: DEFAULT_EVENT_ROUTE,
        OPT_REGISTER_PUSH_ON_DEVICE: False,
        OPT_ALSO_RUN_STREAM: False,
        OPT_RECONCILE_INTERVAL: DEFAULT_RECONCILE_INTERVAL_S,
        OPT_IMAGE_RETENTION_DAYS: DEFAULT_IMAGE_RETENTION_DAYS,
        OPT_STORE_GRANTED_IMAGES: True,
        OPT_STORE_DENIED_IMAGES: True,
        OPT_STORE_RAW_PAYLOAD: False,
        OPT_CREATE_OPEN_DOOR_BUTTON: True,
        OPT_ENABLE_CAMERA: True,
        OPT_RTSP_PORT: DEFAULT_RTSP_PORT,
        OPT_CALL_POLL_INTERVAL: DEFAULT_CALL_POLL_INTERVAL_S,
        OPT_REQUEST_TIMEOUT: DEFAULT_REQUEST_TIMEOUT_S,
        OPT_MASK_CARD_NUMBER: True,
    }


class HikvisionAccessOptionsFlow(OptionsFlow):
    """Runtime tuning (spec §19)."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = {**_default_options(), **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_EVENT_ROUTE, default=opts[OPT_EVENT_ROUTE]
                    ): vol.In(["push", "stream"]),
                    vol.Required(
                        OPT_REGISTER_PUSH_ON_DEVICE,
                        default=opts[OPT_REGISTER_PUSH_ON_DEVICE],
                    ): bool,
                    vol.Required(
                        OPT_ALSO_RUN_STREAM, default=opts[OPT_ALSO_RUN_STREAM]
                    ): bool,
                    vol.Required(
                        OPT_RECONCILE_INTERVAL, default=opts[OPT_RECONCILE_INTERVAL]
                    ): vol.All(
                        int,
                        vol.Range(
                            min=MIN_RECONCILE_INTERVAL_S, max=MAX_RECONCILE_INTERVAL_S
                        ),
                    ),
                    vol.Required(
                        OPT_IMAGE_RETENTION_DAYS,
                        default=opts[OPT_IMAGE_RETENTION_DAYS],
                    ): vol.All(int, vol.Range(min=0, max=3650)),
                    vol.Required(
                        OPT_STORE_GRANTED_IMAGES,
                        default=opts[OPT_STORE_GRANTED_IMAGES],
                    ): bool,
                    vol.Required(
                        OPT_STORE_DENIED_IMAGES,
                        default=opts[OPT_STORE_DENIED_IMAGES],
                    ): bool,
                    vol.Required(
                        OPT_STORE_RAW_PAYLOAD, default=opts[OPT_STORE_RAW_PAYLOAD]
                    ): bool,
                    vol.Required(
                        OPT_CREATE_OPEN_DOOR_BUTTON,
                        default=opts[OPT_CREATE_OPEN_DOOR_BUTTON],
                    ): bool,
                    vol.Required(
                        OPT_ENABLE_CAMERA, default=opts[OPT_ENABLE_CAMERA]
                    ): bool,
                    vol.Required(
                        OPT_RTSP_PORT, default=opts[OPT_RTSP_PORT]
                    ): vol.All(int, vol.Range(min=1, max=65535)),
                    vol.Required(
                        OPT_CALL_POLL_INTERVAL,
                        default=opts[OPT_CALL_POLL_INTERVAL],
                    ): vol.All(
                        int,
                        vol.Range(
                            min=MIN_CALL_POLL_INTERVAL_S, max=MAX_CALL_POLL_INTERVAL_S
                        ),
                    ),
                    vol.Required(
                        OPT_MASK_CARD_NUMBER, default=opts[OPT_MASK_CARD_NUMBER]
                    ): bool,
                }
            ),
        )
