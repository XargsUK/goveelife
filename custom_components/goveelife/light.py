"""Sensor entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio
import math

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util.color import brightness_to_value, value_to_brightness
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    SUPPORT_EFFECT,
)
from homeassistant.const import (
    CONF_DEVICES,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
)

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue, async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)
platform = 'light'
platform_device_types = ['devices.types.light']

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    """Set up the light platform."""
    _LOGGER.debug("Setting up %s platform entry: %s | %s", platform, DOMAIN, entry.entry_id)
    entities = []
        
    try:
        _LOGGER.debug("%s - async_setup_entry %s: Getting cloud devices from data store", entry.entry_id, platform)
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_devices = entry_data[CONF_DEVICES]
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry %s: Getting cloud devices from data store failed: %s (%s.%s)", entry.entry_id, platform, str(e), e.__class__.__module__, type(e).__name__)
        return False

    for device_cfg in api_devices:
        try:
            if not device_cfg.get('type', STATE_UNKNOWN) in platform_device_types:
                continue      
            d = device_cfg.get('device')
            _LOGGER.debug("%s - async_setup_entry %s: Setup device: %s", entry.entry_id, platform, d) 
            coordinator = entry_data[CONF_COORDINATORS][d]
            entity = GoveeLifeLight(hass, entry, coordinator, device_cfg, platform=platform)
            entities.append(entity)
            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error("%s - async_setup_entry %s: Setup device failed: %s (%s.%s)", entry.entry_id, platform, str(e), e.__class__.__module__, type(e).__name__)
            return False

    _LOGGER.info("%s - async_setup_entry: setup %s %s entities", entry.entry_id, len(entities), platform)
    if not entities:
        return None
    async_add_entities(entities)

class GoveeLifeLight(LightEntity, GoveeLifePlatformEntity):
    """Light class for Govee Life integration."""

    _state_mapping = {}
    _state_mapping_set = {}
    _attr_supported_color_modes = set()
    _attr_effect_list = None
    _scene_mapping = {}

    def _init_platform_specific(self, **kwargs):
        """Platform specific init actions"""
        _LOGGER.debug("%s - %s: _init_platform_specific", self._api_id, self._identifier)
        capabilities = self._device_cfg.get('capabilities', [])
        
        # Initialize effects list and mapping at start
        self._attr_effect_list = []
        self._scene_mapping = {}
        self._attr_supported_features = 0  # Initialize supported features

        for cap in capabilities:
            if cap['type'] == 'devices.capabilities.on_off':
                self._attr_supported_color_modes.add(ColorMode.ONOFF)
            elif cap['type'] == 'devices.capabilities.range' and cap['instance'] == 'brightness':
                self._attr_supported_color_modes.add(ColorMode.BRIGHTNESS)
            elif cap['type'] == 'devices.capabilities.color_setting' and cap['instance'] == 'colorRgb':
                self._attr_supported_color_modes.add(ColorMode.RGB)
            elif cap['type'] == 'devices.capabilities.color_setting' and cap['instance'] == 'colorTemperatureK':
                self._attr_supported_color_modes.add(ColorMode.COLOR_TEMP)
            elif cap['type'] == 'devices.capabilities.dynamic_scene':
                self._attr_supported_features |= SUPPORT_EFFECT  # Add support for effects
                _LOGGER.debug("%s - %s: Processing dynamic scene capability: %s", 
                             self._api_id, self._identifier, cap)
                
                options = cap['parameters'].get('options', [])
                scene_instance = cap['instance']
                
                _LOGGER.debug("%s - %s: Found %d options for scene type %s", 
                             self._api_id, self._identifier, len(options), scene_instance)
                
                for option in options:
                    if 'name' in option and 'value' in option:
                        scene_name = f"{scene_instance}_{option['name']}"
                        self._attr_effect_list.append(scene_name)
                        self._scene_mapping[scene_name] = {
                            'type': 'devices.capabilities.dynamic_scene',
                            'instance': scene_instance,
                            'value': option['value']
                        }
                        _LOGGER.debug("%s - %s: Added scene: %s -> %s", 
                                    self._api_id, self._identifier, scene_name, option['value'])

    def _getRGBfromI(self, RGBint):
        blue = RGBint & 255
        green = (RGBint >> 8) & 255
        red = (RGBint >> 16) & 255
        return red, green, blue

    def _getIfromRGB(self, rgb):
        red = rgb[0]
        green = rgb[1]
        blue = rgb[2]
        RGBint = (red << 16) + (green << 8) + blue
        return RGBint

    @property
    def state(self) -> str | None:
        """Return the current state of the entity."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.on_off', 'powerSwitch')
        v = self._state_mapping.get(value, STATE_UNKNOWN)
        if v == STATE_UNKNOWN:
            _LOGGER.warning("%s - %s: state: invalid value: %s", self._api_id, self._identifier, value)
            _LOGGER.debug("%s - %s: state: valid are: %s", self._api_id, self._identifier, self._state_mapping)
        return v

    @property
    def is_on(self) -> bool:
        """Return true if entity is on."""
        return self.state == STATE_ON

    @property
    def brightness(self) -> int | None:
        """Return the current brightness."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.range', 'brightness')
        return value_to_brightness(self._brightness_scale, value)

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return the color temperature in Kelvin."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.color_setting', 'colorTemperatureK')
        return value

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the rgb color."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.color_setting', 'colorRgb')
        return self._getRGBfromI(value)

    @property
    def effect(self) -> str | None:
        """Return the current effect."""
        if not self._attr_effect_list:
            _LOGGER.debug("%s - %s: No effects available", self._api_id, self._identifier)
            return None
        
        # We need to check each scene type (lightScene, diyScene, snapshot)
        for scene_type in ['lightScene', 'diyScene', 'snapshot']:
            value = GoveeAPI_GetCachedStateValue(
                self.hass,
                self._entry_id, 
                self._device_cfg.get('device'),
                'devices.capabilities.dynamic_scene',
                scene_type
            )
            
            _LOGGER.debug("%s - %s: Checking scene type %s, value: %s", 
                         self._api_id, self._identifier, scene_type, value)
            
            if value is not None:
                for scene_name, scene_data in self._scene_mapping.items():
                    if scene_data['instance'] == scene_type and scene_data['value'] == value:
                        _LOGGER.debug("%s - %s: Found active scene: %s", 
                                    self._api_id, self._identifier, scene_name)
                        return scene_name
        
        return None

    @property
    def effect_list(self) -> list[str] | None:
        """Return the list of supported effects."""
        _LOGGER.debug("%s - %s: Returning effect list: %s", self._api_id, self._identifier, self._attr_effect_list)
        return self._attr_effect_list

    @property
    def supported_features(self) -> int:
        """Flag supported features."""
        return self._attr_supported_features

    async def async_turn_on(self, **kwargs) -> None:
        """Async: Turn entity on"""
        try:
            _LOGGER.debug("%s - %s: async_turn_on", self._api_id, self._identifier)
            _LOGGER.debug("%s - %s: async_turn_on: kwargs = %s", self._api_id, self._identifier, kwargs)
            
            if ATTR_EFFECT in kwargs:
                scene_data = self._scene_mapping.get(kwargs[ATTR_EFFECT])
                if scene_data is not None:
                    state_capability = {
                        "type": "devices.capabilities.dynamic_scene",
                        "instance": scene_data['instance'],
                        "value": scene_data['value']
                    }
                    _LOGGER.debug("%s - %s: Setting scene: %s", 
                                 self._api_id, self._identifier, state_capability)
                    if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                        self.async_write_ha_state()

            if ATTR_BRIGHTNESS in kwargs:
                state_capability = {
                    "type": "devices.capabilities.range",
                    "instance": 'brightness',
                    "value": math.ceil(brightness_to_value(self._brightness_scale, kwargs[ATTR_BRIGHTNESS]))   
                }
                if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                    self.async_write_ha_state()

            if ATTR_COLOR_TEMP_KELVIN in kwargs:
                state_capability = {
                    "type": "devices.capabilities.color_setting",
                    "instance": 'colorTemperatureK',
                    "value": kwargs[ATTR_COLOR_TEMP_KELVIN]
                }
                if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                    self.async_write_ha_state()

            if ATTR_RGB_COLOR in kwargs:
                state_capability = {
                    "type": "devices.capabilities.color_setting",
                    "instance": 'colorRgb',
                    "value": self._getIfromRGB(kwargs[ATTR_RGB_COLOR])
                }
                if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                    self.async_write_ha_state()
            
            if not self.is_on:
                state_capability = {
                    "type": "devices.capabilities.on_off",
                    "instance": 'powerSwitch',
                    "value": self._state_mapping_set[STATE_ON]
                }
                if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                    self.async_write_ha_state()
            else:
                _LOGGER.debug("%s - %s: async_turn_on: device already on", self._api_id, self._identifier)
        except Exception as e:
            _LOGGER.error("%s - %s: async_turn_on failed: %s (%s.%s)", self._api_id, self._identifier, str(e), e.__class__.__module__, type(e).__name__)

    async def async_turn_off(self, **kwargs) -> None:
        """Async: Turn entity off"""
        try:
            _LOGGER.debug("%s - %s: async_turn_off", self._api_id, self._identifier)
            _LOGGER.debug("%s - %s: async_turn_off: kwargs = %s", self._api_id, self._identifier, kwargs)
            if self.is_on:
                state_capability = {
                    "type": "devices.capabilities.on_off",
                    "instance": 'powerSwitch',
                    "value": self._state_mapping_set[STATE_OFF]
                }
                if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
                    self.async_write_ha_state()
            else:
                _LOGGER.debug("%s - %s: async_turn_on: device already off", self._api_id, self._identifier)
        except Exception as e:
            _LOGGER.error("%s - %s: async_turn_off failed: %s (%s.%s)", self._api_id, self._identifier, str(e), e.__class__.__module__, type(e).__name__)
