"""Constants for the EZVIZ Light integration."""

DOMAIN = "ezviz_light"
MANUFACTURER = "EZVIZ"

CONF_SESSION_ID = "session_id"
CONF_RFSESSION_ID = "rf_session_id"

EU_URL = "apiieu.ezvizlife.com"
RUSSIA_URL = "apirus.ezvizru.com"
OPTION_CUSTOM_URL = "custom"

DEFAULT_TIMEOUT = 25
UPDATE_INTERVAL_SECONDS = 30

# LB1-Color's CCT range - only a fallback now; state.py prefers the
# device's own reported range via dataDesc, confirmed live to be exactly
# this (literal Kelvin, "unit": "k"). LB1-White has no color_temperature
# feature at all.
EZVIZ_MIN_COLOR_TEMP_KELVIN = 2700
EZVIZ_MAX_COLOR_TEMP_KELVIN = 6500

# Confirmed directly off a live LB1-Color's raw feature list - both the
# read and write key for each feature (pyezvizapi's own set_brightness
# hardcodes "brightness" the same way internally).
FEATURE_KEY_LIGHT_SWITCH = "light_switch"
FEATURE_KEY_BRIGHTNESS = "brightness"
FEATURE_KEY_COLOR_TEMPERATURE = "color_temperature"

# Confirmed directly off a live LB1-Color's raw feature list: itemKey
# "color_rgb", dataType "char", transportType "rw", value like "#43FF49".
FEATURE_KEY_COLOR_RGB = "color_rgb"

# Confirmed directly off a live LB1-Color's raw feature list: itemKey
# "light_mode", dataType "enum" of ["white", "color", "scene", "music"],
# transportType "rw". Selects which of color_temperature/color_rgb is
# actually in effect; written alongside color_rgb/color_temperature so the
# device actually switches to render the one just set.
FEATURE_KEY_LIGHT_MODE = "light_mode"
LIGHT_MODE_WHITE = "white"
LIGHT_MODE_COLOR = "color"
