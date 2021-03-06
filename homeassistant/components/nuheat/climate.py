"""Support for NuHeat thermostats."""
from datetime import timedelta
import logging

from nuheat.config import SCHEDULE_HOLD, SCHEDULE_RUN, SCHEDULE_TEMPORARY_HOLD
import voluptuous as vol

from homeassistant.components.climate import ClimateDevice
from homeassistant.components.climate.const import (
    CURRENT_HVAC_HEAT,
    CURRENT_HVAC_IDLE,
    HVAC_MODE_AUTO,
    HVAC_MODE_HEAT,
    SUPPORT_PRESET_MODE,
    SUPPORT_TARGET_TEMPERATURE,
)
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_TEMPERATURE,
    TEMP_CELSIUS,
    TEMP_FAHRENHEIT,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.util import Throttle

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)

MIN_TIME_BETWEEN_UPDATES = timedelta(minutes=5)

# The device does not have an off function.
# To turn it off set to min_temp and PRESET_PERMANENT_HOLD
OPERATION_LIST = [HVAC_MODE_AUTO, HVAC_MODE_HEAT]

PRESET_RUN = "Run Schedule"
PRESET_TEMPORARY_HOLD = "Temporary Hold"
PRESET_PERMANENT_HOLD = "Permanent Hold"

PRESET_MODES = [PRESET_RUN, PRESET_TEMPORARY_HOLD, PRESET_PERMANENT_HOLD]

PRESET_MODE_TO_SCHEDULE_MODE_MAP = {
    PRESET_RUN: SCHEDULE_RUN,
    PRESET_TEMPORARY_HOLD: SCHEDULE_TEMPORARY_HOLD,
    PRESET_PERMANENT_HOLD: SCHEDULE_HOLD,
}

SCHEDULE_MODE_TO_PRESET_MODE_MAP = {
    value: key for key, value in PRESET_MODE_TO_SCHEDULE_MODE_MAP.items()
}

SERVICE_RESUME_PROGRAM = "resume_program"

RESUME_PROGRAM_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.entity_ids})

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_PRESET_MODE


def setup_platform(hass, config, add_entities, discovery_info=None):
    """Set up the NuHeat thermostat(s)."""
    if discovery_info is None:
        return

    temperature_unit = hass.config.units.temperature_unit
    api, serial_numbers = hass.data[DOMAIN]
    thermostats = [
        NuHeatThermostat(api, serial_number, temperature_unit)
        for serial_number in serial_numbers
    ]
    add_entities(thermostats, True)

    def resume_program_set_service(service):
        """Resume the program on the target thermostats."""
        entity_id = service.data.get(ATTR_ENTITY_ID)
        if entity_id:
            target_thermostats = [
                device for device in thermostats if device.entity_id in entity_id
            ]
        else:
            target_thermostats = thermostats

        for thermostat in target_thermostats:
            thermostat.resume_program()

            thermostat.schedule_update_ha_state(True)

    hass.services.register(
        DOMAIN,
        SERVICE_RESUME_PROGRAM,
        resume_program_set_service,
        schema=RESUME_PROGRAM_SCHEMA,
    )


class NuHeatThermostat(ClimateDevice):
    """Representation of a NuHeat Thermostat."""

    def __init__(self, api, serial_number, temperature_unit):
        """Initialize the thermostat."""
        self._thermostat = api.get_thermostat(serial_number)
        self._temperature_unit = temperature_unit
        self._force_update = False

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._thermostat.room

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_FLAGS

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        if self._temperature_unit == "C":
            return TEMP_CELSIUS

        return TEMP_FAHRENHEIT

    @property
    def current_temperature(self):
        """Return the current temperature."""
        if self._temperature_unit == "C":
            return self._thermostat.celsius

        return self._thermostat.fahrenheit

    @property
    def unique_id(self):
        """Return the unique id."""
        return self._thermostat.serial_number

    @property
    def available(self):
        """Return the unique id."""
        return self._thermostat.online

    def set_hvac_mode(self, hvac_mode):
        """Set the system mode."""

        if hvac_mode == HVAC_MODE_AUTO:
            self._thermostat.schedule_mode = SCHEDULE_RUN
        elif hvac_mode == HVAC_MODE_HEAT:
            self._thermostat.schedule_mode = SCHEDULE_HOLD

        self._schedule_update()

    @property
    def hvac_mode(self):
        """Return current setting heat or auto."""
        if self._thermostat.schedule_mode in (SCHEDULE_TEMPORARY_HOLD, SCHEDULE_HOLD):
            return HVAC_MODE_HEAT
        return HVAC_MODE_AUTO

    @property
    def hvac_action(self):
        """Return current operation heat or idle."""
        return CURRENT_HVAC_HEAT if self._thermostat.heating else CURRENT_HVAC_IDLE

    @property
    def min_temp(self):
        """Return the minimum supported temperature for the thermostat."""
        if self._temperature_unit == "C":
            return self._thermostat.min_celsius

        return self._thermostat.min_fahrenheit

    @property
    def max_temp(self):
        """Return the maximum supported temperature for the thermostat."""
        if self._temperature_unit == "C":
            return self._thermostat.max_celsius

        return self._thermostat.max_fahrenheit

    @property
    def target_temperature(self):
        """Return the currently programmed temperature."""
        if self._temperature_unit == "C":
            return self._thermostat.target_celsius

        return self._thermostat.target_fahrenheit

    @property
    def preset_mode(self):
        """Return current preset mode."""
        schedule_mode = self._thermostat.schedule_mode
        return SCHEDULE_MODE_TO_PRESET_MODE_MAP.get(schedule_mode, PRESET_RUN)

    @property
    def preset_modes(self):
        """Return available preset modes."""
        return PRESET_MODES

    @property
    def hvac_modes(self):
        """Return list of possible operation modes."""
        return OPERATION_LIST

    def resume_program(self):
        """Resume the thermostat's programmed schedule."""
        self._thermostat.resume_schedule()
        self._schedule_update()

    def set_preset_mode(self, preset_mode):
        """Update the hold mode of the thermostat."""

        self._thermostat.schedule_mode = PRESET_MODE_TO_SCHEDULE_MODE_MAP.get(
            preset_mode, SCHEDULE_RUN
        )
        self._schedule_update()

    def set_temperature(self, **kwargs):
        """Set a new target temperature."""
        self._set_temperature(kwargs.get(ATTR_TEMPERATURE))

    def _set_temperature(self, temperature):
        if self._temperature_unit == "C":
            self._thermostat.target_celsius = temperature
        else:
            self._thermostat.target_fahrenheit = temperature
        # If they set a temperature without changing the mode
        # to heat, we behave like the device does locally
        # and set a temp hold.
        if self._thermostat.schedule_mode == SCHEDULE_RUN:
            self._thermostat.schedule_mode = SCHEDULE_TEMPORARY_HOLD

        _LOGGER.debug(
            "Setting NuHeat thermostat temperature to %s %s",
            temperature,
            self.temperature_unit,
        )
        self._schedule_update()

    def _schedule_update(self):
        self._force_update = True
        if self.hass:
            self.schedule_update_ha_state(True)

    def update(self):
        """Get the latest state from the thermostat."""
        if self._force_update:
            self._throttled_update(no_throttle=True)
            self._force_update = False
        else:
            self._throttled_update()

    @Throttle(MIN_TIME_BETWEEN_UPDATES)
    def _throttled_update(self, **kwargs):
        """Get the latest state from the thermostat with a throttle."""
        self._thermostat.get_data()
