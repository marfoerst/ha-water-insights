"""Constants for Water Insights."""
from __future__ import annotations

DOMAIN = "water_insights"

CONF_SOURCE_ENTITY = "source_entity"
CONF_SOURCE_MODE = "source_mode"
CONF_COUNTER_FACTOR = "counter_factor"
CONF_CATEGORIES = "categories"
CONF_START_FLOW = "start_flow"
CONF_END_QUIET_S = "end_quiet_s"
CONF_MIN_VOLUME_L = "min_volume_l"
CONF_HELPER_ENTITIES = "helper_entities"
CONF_CONF_THRESHOLD = "conf_threshold"
CONF_BACKFILL = "backfill"

MODE_FLOW = "flow"
MODE_COUNTER = "counter"

DEFAULT_START_FLOW = 1.0
DEFAULT_END_QUIET_S = 60.0
DEFAULT_MIN_VOLUME_L = 2.0
DEFAULT_MAX_FLOW = 60.0
DEFAULT_CONF_THRESHOLD = 0.8
DEFAULT_MIN_PER_CLASS = 10
DEFAULT_MIN_PRECISION = 0.9
DEFAULT_CATEGORIES = [
    "Dusche",
    "Toilette",
    "Waschmaschine",
    "Spülmaschine",
    "Wasserhahn",
    "Garten",
]

LABEL_MIXED = "mixed"
LABEL_UNCLASSIFIED = "unclassified"
LABEL_SMALL = "small"

SOURCE_USER = "user"
SOURCE_AUTO = "auto"

STATUS_OPEN = "open"
STATUS_AUTO = "auto"
STATUS_CONFIRMED = "confirmed"

EVENT_DETECTED = "water_insights_event_detected"
EVENT_CLASSIFIED = "water_insights_event_classified"

# Units: flow units -> factor to L/min; counter units -> factor to L
FLOW_UNITS = {"L/min": 1.0, "L/h": 1 / 60, "m³/h": 1000 / 60, "gal/min": 3.78541}
COUNTER_UNITS = {"L": 1.0, "m³": 1000.0, "gal": 3.78541}

MAX_SAMPLES = 400
SLOW_SENSOR_INTERVAL_S = 60.0
SLOW_SENSOR_MIN_SAMPLES = 50
