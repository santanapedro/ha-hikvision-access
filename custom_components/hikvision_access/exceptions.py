"""Exception hierarchy for the Hikvision Access integration (spec §33)."""

from __future__ import annotations


class HikvisionError(Exception):
    """Base class for every error raised by this integration."""


class HikvisionAuthError(HikvisionError):
    """Credentials rejected, or the account may not use ISAPI."""


class HikvisionLockoutError(HikvisionAuthError):
    """Terminal has temporarily locked logins after too many failed attempts.

    Carries the remaining lock time in seconds when the device reports it
    (``<userCheck><lockStatus>lock</lockStatus><unlockTime>N</unlockTime>``).
    """

    def __init__(self, unlock_seconds: int | None = None) -> None:
        self.unlock_seconds = unlock_seconds
        detail = f" (unlock in ~{unlock_seconds}s)" if unlock_seconds is not None else ""
        super().__init__(f"Terminal locked out logins{detail}")


class HikvisionConnectionError(HikvisionError):
    """Could not reach the terminal (DNS, refused, reset, TLS)."""


class HikvisionTimeoutError(HikvisionConnectionError):
    """A request or the event stream timed out."""


class HikvisionUnsupportedError(HikvisionError):
    """The terminal does not support a required capability/endpoint."""


class HikvisionStreamBusyError(HikvisionConnectionError):
    """alertStream returned 404 — a previous connection's slot is still held.

    Some firmwares hold an alertStream slot for a long time after the client
    disconnects and 404 every new connection until it frees. Back off well
    beyond the normal reconnect cadence; the reconciler covers events meanwhile.
    """


class HikvisionProtocolError(HikvisionError):
    """The terminal answered, but the payload could not be understood."""
