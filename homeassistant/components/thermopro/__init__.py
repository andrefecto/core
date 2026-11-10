"""The ThermoPro Bluetooth integration."""

from functools import partial
import logging

from thermopro_ble import SensorUpdate, ThermoProBluetoothDeviceData

from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_MODEL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import DOMAIN, SIGNAL_DATA_UPDATED
from .coordinator import ThermoProActiveCoordinator

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR]
# Models that report over a connection have no date/time button.
ACTIVE_PLATFORMS: list[Platform] = [Platform.SENSOR]

_LOGGER = logging.getLogger(__name__)

type ThermoProConfigEntry = ConfigEntry[
    PassiveBluetoothProcessorCoordinator | ThermoProActiveCoordinator
]


def process_service_info(
    hass: HomeAssistant,
    entry: ThermoProConfigEntry,
    data: ThermoProBluetoothDeviceData,
    service_info: BluetoothServiceInfoBleak,
) -> SensorUpdate:
    """Process a BluetoothServiceInfoBleak and return sensor data."""
    update = data.update(service_info)
    async_dispatcher_send(
        hass, f"{SIGNAL_DATA_UPDATED}_{entry.entry_id}", data, service_info, update
    )
    return update


async def async_setup_entry(hass: HomeAssistant, entry: ThermoProConfigEntry) -> None:
    """Set up ThermoPro BLE device from a config entry."""
    address = entry.unique_id
    assert address is not None
    if model := entry.data.get(CONF_MODEL):
        active = ThermoProActiveCoordinator(hass, entry, address, model)
        entry.runtime_data = active
        # Not first_refresh: the thermometer is normally off, which must not
        # fail setup. The coordinator connects when it shows up.
        await active.async_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, ACTIVE_PLATFORMS)
        return
    data = ThermoProBluetoothDeviceData()
    entry.runtime_data = coordinator = PassiveBluetoothProcessorCoordinator(
        hass,
        _LOGGER,
        address=address,
        mode=BluetoothScanningMode.ACTIVE,
        update_method=partial(process_service_info, hass, entry, data),
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # only start after all platforms have had a chance to subscribe
    entry.async_on_unload(coordinator.async_start())


async def async_unload_entry(hass: HomeAssistant, entry: ThermoProConfigEntry) -> None:
    """Unload a config entry."""
    platforms = ACTIVE_PLATFORMS if entry.data.get(CONF_MODEL) else PLATFORMS
    await hass.config_entries.async_unload_platforms_or_raise(entry, platforms)
