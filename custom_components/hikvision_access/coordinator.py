"""Health / door-status coordinator for one terminal (spec §20, §22).

The realtime event path (push view or alertStream listener) runs as its own
task and does NOT go through here. This coordinator only polls the slow,
periodic things: relay/door state, connectivity, firmware.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, NoReturn

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HikvisionISAPIClient
from .const import (
    DEFAULT_CALL_POLL_INTERVAL_S,
    DOMAIN,
    EP_ACS_WORK_STATUS,
    HEALTH_POLL_INTERVAL_S,
)
from .exceptions import HikvisionAuthError, HikvisionError, HikvisionLockoutError

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class HealthData:
    """Snapshot from AcsWorkStatus plus listener-reported health."""

    reachable: bool = False
    door_locked: bool | None = None
    door_open: bool | None = None
    tamper: bool | None = None
    net_status: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class _LockAwareCoordinator(DataUpdateCoordinator):
    """Skips the network entirely while the client is parked in a lockout, and
    stretches its own poll interval to the unlock time so it stops hammering."""

    _base_interval: timedelta

    def _guard_lock(self) -> None:
        remaining = self.client.lock_remaining
        if remaining:
            self.update_interval = timedelta(seconds=min(remaining + 5, 300))
            raise UpdateFailed(f"terminal locked out (~{remaining}s)")
        if self.update_interval != self._base_interval:
            self.update_interval = self._base_interval

    def _on_lockout(self, err: HikvisionLockoutError) -> NoReturn:
        secs = err.unlock_seconds or 180
        self.update_interval = timedelta(seconds=min(secs + 5, 300))
        raise UpdateFailed(str(err)) from err


class HikvisionHealthCoordinator(_LockAwareCoordinator):
    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: HikvisionISAPIClient
    ) -> None:
        self._base_interval = timedelta(seconds=HEALTH_POLL_INTERVAL_S)
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_health", update_interval=self._base_interval
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> HealthData:
        self._guard_lock()
        try:
            raw = await self.client.async_get_caps(EP_ACS_WORK_STATUS)
        except HikvisionLockoutError as err:
            self._on_lockout(err)
        except HikvisionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HikvisionError as err:
            raise UpdateFailed(str(err)) from err

        status = raw.get("AcsWorkStatus", raw)
        lock = _first(status.get("doorLockStatus"))
        door = _first(status.get("doorStatus"))
        return HealthData(
            reachable=True,
            door_locked=None if lock is None else lock == 0,
            # doorStatus: 0 unknown, 1 open, 2 closed, 4 sleeping/normal — TODO confirm
            door_open=None if door is None else door == 1,
            tamper=str(status.get("hostAntiDismantleStatus", "close")) != "close",
            net_status=status.get("netStatus"),
            raw=status,
        )


_RINGING = {"ring", "ringing", "calling", "bell"}
_ON_CALL = {"oncall", "incall", "talking", "answered"}


class HikvisionCallCoordinator(_LockAwareCoordinator):
    """Poll of the video-intercom call status (doorbell button).

    Fallback for terminals whose call event does not arrive on the alertStream.
    State is the raw status string, lowercased ('idle', 'ring', 'oncall', ...).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: HikvisionISAPIClient,
        interval_s: int = DEFAULT_CALL_POLL_INTERVAL_S,
    ) -> None:
        self._base_interval = timedelta(seconds=interval_s)
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_call", update_interval=self._base_interval
        )
        self.entry = entry
        self.client = client

    async def _async_update_data(self) -> str:
        self._guard_lock()
        try:
            status = await self.client.async_get_call_status()
        except HikvisionLockoutError as err:
            self._on_lockout(err)
        except HikvisionError as err:
            raise UpdateFailed(str(err)) from err
        return (status or "idle").strip().lower()

    @property
    def is_ringing(self) -> bool:
        return (self.data or "") in _RINGING

    @property
    def in_call(self) -> bool:
        return (self.data or "") in _ON_CALL


def _first(value: Any) -> int | None:
    if isinstance(value, list) and value:
        try:
            return int(value[0])
        except (TypeError, ValueError):
            return None
    if isinstance(value, (int, str)):
        try:
            return int(value)
        except ValueError:
            return None
    return None
