"""Constants for the Hikvision Access integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "hikvision_access"

# --- config entry keys ---
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_USE_HTTPS: Final = "use_https"
CONF_VERIFY_SSL: Final = "verify_ssl"
CONF_NAME: Final = "name"

# --- options keys ---
OPT_EVENT_ROUTE: Final = "event_route"          # "push" | "stream"
OPT_RECONCILE_INTERVAL: Final = "reconcile_interval_s"
OPT_IMAGE_RETENTION_DAYS: Final = "image_retention_days"
OPT_STORE_GRANTED_IMAGES: Final = "store_granted_images"
OPT_STORE_DENIED_IMAGES: Final = "store_denied_images"
OPT_STORE_RAW_PAYLOAD: Final = "store_raw_payload"
OPT_CREATE_OPEN_DOOR_BUTTON: Final = "create_open_door_button"
OPT_REQUEST_TIMEOUT: Final = "request_timeout_s"
OPT_MASK_CARD_NUMBER: Final = "mask_card_number"
OPT_REGISTER_PUSH_ON_DEVICE: Final = "register_push_on_device"
OPT_ALSO_RUN_STREAM: Final = "also_run_stream"

# stored in the config entry after we successfully claim a push slot
DATA_PUSH_TOKEN: Final = "push_token"
DATA_PUSH_SLOT: Final = "push_slot"

# --- defaults ---
DEFAULT_NAME: Final = "Hikvision Access"
DEFAULT_HTTPS_PORT: Final = 443
DEFAULT_HTTP_PORT: Final = 80
DEFAULT_USE_HTTPS: Final = True
DEFAULT_VERIFY_SSL: Final = False

DEFAULT_EVENT_ROUTE: Final = "push"
DEFAULT_RECONCILE_INTERVAL_S: Final = 60
MIN_RECONCILE_INTERVAL_S: Final = 30
MAX_RECONCILE_INTERVAL_S: Final = 900
RECONCILE_OVERLAP_S: Final = 120

DEFAULT_IMAGE_RETENTION_DAYS: Final = 90
DEFAULT_REQUEST_TIMEOUT_S: Final = 10
STREAM_CONNECT_TIMEOUT_S: Final = 15
EVENT_IDLE_TIMEOUT_S: Final = 90

# health coordinator polling (door status / firmware / connectivity)
HEALTH_POLL_INTERVAL_S: Final = 30

# --- reconnect backoff for the alertStream listener (spec §9.2) ---
RECONNECT_BACKOFF_S: Final = (1, 2, 5, 10, 30, 60)

# --- event bus event fired for every normalized access event (spec §15.4) ---
EVENT_BUS_EVENT: Final = "hikvision_access_event"

# --- normalized access results (spec §7.1) ---
RESULT_GRANTED: Final = "granted"
RESULT_DENIED: Final = "denied"
RESULT_UNKNOWN: Final = "unknown"

# --- normalized authentication methods (spec §7.2) ---
METHOD_FACE: Final = "face"
METHOD_CARD: Final = "card"
METHOD_FINGERPRINT: Final = "fingerprint"
METHOD_QR: Final = "qr"
METHOD_PASSWORD: Final = "password"
METHOD_FACE_CARD: Final = "face_card"
METHOD_FACE_FP: Final = "face_fingerprint"
METHOD_REMOTE: Final = "remote"
METHOD_BUTTON: Final = "button"
METHOD_OTHER: Final = "other"
METHOD_UNKNOWN: Final = "unknown"

# --- normalized event-entity types (spec §15.3) ---
EVENT_ACCESS_GRANTED: Final = "access_granted"
EVENT_ACCESS_DENIED: Final = "access_denied"
EVENT_DOOR_OPENED: Final = "door_opened"
EVENT_DOOR_CLOSED: Final = "door_closed"
EVENT_TAMPER: Final = "tamper"
EVENT_DEVICE_ALARM: Final = "device_alarm"

# --- ISAPI endpoints (validated on DS-K1T342MWX FW V3.16.1 — see docs/DISCOVERY) ---
EP_DEVICE_INFO: Final = "/ISAPI/System/deviceInfo"
EP_SYSTEM_TIME: Final = "/ISAPI/System/time"
EP_ALERT_STREAM: Final = "/ISAPI/Event/notification/alertStream"
EP_HTTP_HOSTS: Final = "/ISAPI/Event/notification/httpHosts"
EP_ACS_EVENT: Final = "/ISAPI/AccessControl/AcsEvent?format=json"
EP_ACS_EVENT_CAPS: Final = "/ISAPI/AccessControl/AcsEvent/capabilities?format=json"
EP_ACS_CAPS: Final = "/ISAPI/AccessControl/capabilities?format=json"
EP_ACS_WORK_STATUS: Final = "/ISAPI/AccessControl/AcsWorkStatus?format=json"
EP_DOOR_PARAM: Final = "/ISAPI/AccessControl/Door/param/{door}?format=json"
EP_REMOTE_DOOR: Final = "/ISAPI/AccessControl/RemoteControl/door/{door}"
EP_REMOTE_DOOR_CAPS: Final = "/ISAPI/AccessControl/RemoteControl/door/capabilities?format=json"
EP_USER_COUNT: Final = "/ISAPI/AccessControl/UserInfo/Count?format=json"
EP_USER_SEARCH: Final = "/ISAPI/AccessControl/UserInfo/Search?format=json"
EP_USER_CAPS: Final = "/ISAPI/AccessControl/UserInfo/capabilities?format=json"

# AcsEvent search: this firmware caps a page at 30 results
ACS_EVENT_MAX_RESULTS: Final = 30
