"""Constants for Aruba Radio Tuner."""

from datetime import timedelta

DOMAIN = "aruba_radio_tuner"
PLATFORMS = ["sensor"]
CONF_VERIFY_SSL = "verify_ssl"
DEFAULT_PORT = 4343
DEFAULT_RECOVERY_SECONDS = 120
DEFAULT_UPDATE_INTERVAL = timedelta(seconds=30)
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_state"
CARD_FILENAME = "aruba-radio-tuner-card.js"
STATIC_URL = f"/api/{DOMAIN}/static"

SERVICE_START_TEST = "start_test"
SERVICE_APPLY = "apply_recommendation"
SERVICE_SCHEDULE = "schedule_test"
SERVICE_CANCEL_SCHEDULE = "cancel_schedule"
SERVICE_DELETE_RESULT = "delete_result"
SERVICE_CANCEL_TEST = "cancel_test"
