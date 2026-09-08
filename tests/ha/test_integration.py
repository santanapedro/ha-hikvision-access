"""Load tests that require Home Assistant (pytest-homeassistant-custom-component).

Skipped automatically when HA isn't installed (plain `pytest` in the light env).
Run with the .venv-ha interpreter to exercise these.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)
from custom_components.hikvision_access.models import (
    DeviceCapabilities,
    DeviceInfo,
)


@pytest.fixture
def _bypass_probe():
    info = DeviceInfo(
        model="DS-K1T342MWX",
        serial_number="TESTSERIAL123",
        firmware="V3.16.1",
        mac="A4:D5:C2:00:00:00",
        device_name="LEITOR TESTE",
        device_type="ACS",
    )
    caps = DeviceCapabilities(
        event_stream=True,
        push_notification=True,
        access_event_search=True,
        event_picture=True,
        user_picture=True,
        remote_door_control=True,
        door_status=True,
        user_search=True,
        face=True,
    )
    work_status = {"AcsWorkStatus": {"doorLockStatus": [0], "doorStatus": [4],
                                     "magneticStatus": [0], "netStatus": "connect"}}
    with (
        patch(
            "custom_components.hikvision_access.HikvisionISAPIClient.async_get_device_info",
            AsyncMock(return_value=info),
        ),
        patch(
            "custom_components.hikvision_access.async_discover",
            AsyncMock(return_value=caps),
        ),
        patch(
            "custom_components.hikvision_access._read_door_name",
            AsyncMock(return_value="PORTA DOS FUNDOS"),
        ),
        patch(
            "custom_components.hikvision_access.coordinator.HikvisionISAPIClient.async_get_caps",
            AsyncMock(return_value=work_status),
        ),
        patch(
            "custom_components.hikvision_access.event_reconciler.EventReconciler.async_start",
            AsyncMock(),
        ),
    ):
        yield info, caps




async def test_setup_and_unload(hass: HomeAssistant, _bypass_probe) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Leiteste",
        unique_id="TESTSERIAL123",
        data={
            CONF_HOST: "192.0.2.10",
            CONF_PORT: 443,
            CONF_USERNAME: "ha",
            CONF_PASSWORD: "x",
            CONF_USE_HTTPS: True,
            CONF_VERIFY_SSL: False,
        },
        options={"event_route": "stream", "register_push_on_device": False},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # entities exist
    assert hass.states.get("event.leiteste_acesso") is not None
    assert hass.states.get("binary_sensor.leiteste_online") is not None
    assert hass.states.get("button.leiteste_abrir_porta") is not None
    assert hass.states.get("sensor.leiteste_status_da_conexao") is not None

    # services registered
    assert hass.services.has_service(DOMAIN, "open_door")
    assert hass.services.has_service(DOMAIN, "reconcile_now")

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_config_flow_happy_path(hass: HomeAssistant, _bypass_probe) -> None:
    info, caps = _bypass_probe
    with patch(
        "custom_components.hikvision_access.config_flow._probe",
        AsyncMock(return_value=(info, caps)),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": "user"}
        )
        assert result["type"] == "form"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.0.2.10",
                CONF_USERNAME: "ha",
                CONF_PASSWORD: "secret",
                CONF_USE_HTTPS: True,
                CONF_VERIFY_SSL: False,
            },
        )
    assert result["type"] == "create_entry"
    assert result["result"].unique_id == "TESTSERIAL123"
