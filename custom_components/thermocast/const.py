"""Constants for Thermocast."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "thermocast"
PLATFORMS = ["sensor", "binary_sensor", "switch", "button"]

SUBENTRY_ZONE = "zone"

UPDATE_INTERVAL = timedelta(minutes=15)
FORECAST_MAX_AGE = timedelta(minutes=60)
FORECAST_STALE_FAILSAFE = timedelta(hours=2)
HORIZON_HOURS = 24
STORAGE_VERSION = 1

# --- house (main entry) ---
CONF_OUTDOOR_SENSOR = "outdoor_sensor"
CONF_FLOW_TEMP_SENSOR = "flow_temp_sensor"
CONF_HEATING_ACTIVE = "heating_active_entity"
CONF_RELEASE_ENTITY = "release_entity"
CONF_RELEASE_ON = "release_on_value"
CONF_RELEASE_OFF = "release_off_value"

# --- options ---
CONF_MIN_BLOCK_H = "min_block_hours"
CONF_MIN_PAUSE_H = "min_pause_hours"
CONF_MAX_SWITCHES = "max_switches_per_day"
CONF_FORGETTING = "forgetting_factor"
CONF_CONFIDENCE_Z = "confidence_z"
CONF_CALIBRATE_SIGMA = "calibrate_sigma"  # widen σ from the observed forecast errors
CONF_STARTS_WEIGHT = "starts_weight"  # 0 = little gas … 100 = few burner starts
DEFAULT_STARTS_WEIGHT = 80

DEFAULT_MIN_BLOCK_H = 3
DEFAULT_MIN_PAUSE_H = 2
DEFAULT_MAX_SWITCHES = 12
DEFAULT_FORGETTING = 0.996
DEFAULT_CONFIDENCE_Z = 1.0
DEFAULT_CALIBRATE_SIGMA = True
CALIBRATION_DAYS = 14

# --- zone (subentry) ---
CONF_HEAT_TYPE = "heat_type"  # fbh | radiator
CONF_TEMP_SENSORS = "temp_sensors"
CONF_VALVE_ENTITY = "valve_entity"
CONF_COMFORT_TEMP = "comfort_temp"
CONF_COMFORT_BAND = "comfort_band"
CONF_ACTIVE_FROM = "active_from"
CONF_ACTIVE_TO = "active_to"
CONF_COMFORT_SCHEDULE = "comfort_schedule"  # optional schedule.* entity, replaces from/to
CONF_LEADS_RELEASE = "leads_release"
CONF_SURFACES = "surfaces"
CONF_NEIGHBOR_SENSORS = "neighbor_sensors"
CONF_GAIN_ENTITIES = "gain_entities"
CONF_WINDOW_ENTITIES = "window_entities"

# charge and coast (see docs/superpowers/specs/2026-10-06-charge-and-coast-design.md)
CONF_COMFORT_HIGH = "comfort_high"  # upper bound: a block may charge the zone up to here
CONF_BASE_TEMP = "base_temp"  # lower bound outside comfort time (night/away)
CONF_BT_CONTROL = "bt_control"  # Thermocast sets the Better Thermostat target of this zone
CONF_BT_ENTITY = "bt_entity"  # the Better Thermostat climate entity
CONF_QUIET_FROM = "quiet_from"  # no thermostat writes (valve noise) from …
CONF_QUIET_TO = "quiet_to"  # … until
DEFAULT_HIGH_OFFSET = 1.0  # K above comfort
DEFAULT_BASE_OFFSET = 2.0  # K below comfort
QUIET_LEAD = timedelta(minutes=15)  # set the floor this long before the quiet time starts

HEAT_TYPES = ["fbh", "radiator"]
DEFAULT_Q_ON = 10.0  # K, initial guess of the heating proxy while heating

# --- optional panel sources (options) ---
CONF_DHW_ENTITY = "dhw_active_entity"  # DHW charging active (hatching in the timeline)
CONF_BURNER_STARTS = "burner_starts_entity"  # total_increasing counter (KPIs)
CONF_HEAT_ENERGY = "heat_energy_entity"  # kWh, total_increasing (KPIs)
HDD_BASE = 15.0  # °C heating limit for heating degree days
