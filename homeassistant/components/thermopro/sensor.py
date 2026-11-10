"""Support for thermopro ble sensors."""

from typing import override

from thermopro_ble import (
    DeviceKey,
    SensorDeviceClass as ThermoProSensorDeviceClass,
    SensorUpdate,
    Units,
)
from thermopro_ble.tp902 import probe_count

from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothDataProcessor,
    PassiveBluetoothDataUpdate,
    PassiveBluetoothEntityKey,
    PassiveBluetoothProcessorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.sensor import sensor_device_info_to_hass_device_info
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import ThermoProConfigEntry
from .const import DOMAIN
from .coordinator import ThermoProActiveCoordinator

SENSOR_DESCRIPTIONS = {
    (
        ThermoProSensorDeviceClass.TEMPERATURE,
        Units.TEMP_CELSIUS,
    ): SensorEntityDescription(
        key=f"{ThermoProSensorDeviceClass.TEMPERATURE}_{Units.TEMP_CELSIUS}",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    (ThermoProSensorDeviceClass.HUMIDITY, Units.PERCENTAGE): SensorEntityDescription(
        key=f"{ThermoProSensorDeviceClass.HUMIDITY}_{Units.PERCENTAGE}",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    (
        ThermoProSensorDeviceClass.SIGNAL_STRENGTH,
        Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    ): SensorEntityDescription(
        key=f"{ThermoProSensorDeviceClass.SIGNAL_STRENGTH}_{Units.SIGNAL_STRENGTH_DECIBELS_MILLIWATT}",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    (
        ThermoProSensorDeviceClass.BATTERY,
        Units.PERCENTAGE,
    ): SensorEntityDescription(
        key=f"{ThermoProSensorDeviceClass.BATTERY}_{Units.PERCENTAGE}",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
}


def _device_key_to_bluetooth_entity_key(
    device_key: DeviceKey,
) -> PassiveBluetoothEntityKey:
    """Convert a device key to an entity key."""
    return PassiveBluetoothEntityKey(device_key.key, device_key.device_id)


def sensor_update_to_bluetooth_data_update(
    sensor_update: SensorUpdate,
) -> PassiveBluetoothDataUpdate:
    """Convert a sensor update to a bluetooth data update."""
    return PassiveBluetoothDataUpdate(
        devices={
            device_id: sensor_device_info_to_hass_device_info(device_info)
            for device_id, device_info in sensor_update.devices.items()
        },
        entity_descriptions={
            _device_key_to_bluetooth_entity_key(device_key): SENSOR_DESCRIPTIONS[
                (description.device_class, description.native_unit_of_measurement)
            ]
            for device_key, description in sensor_update.entity_descriptions.items()
            if description.device_class and description.native_unit_of_measurement
        },
        entity_data={
            _device_key_to_bluetooth_entity_key(device_key): sensor_values.native_value
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
        entity_names={
            _device_key_to_bluetooth_entity_key(device_key): sensor_values.name
            for device_key, sensor_values in sensor_update.entity_values.items()
        },
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ThermoProConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the ThermoPro BLE sensors."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, ThermoProActiveCoordinator):
        async_add_entities(
            [
                *(
                    ThermoProProbeSensorEntity(coordinator, probe)
                    for probe in range(1, probe_count(coordinator.model) + 1)
                ),
                ThermoProBatterySensorEntity(coordinator),
            ]
        )
        return
    processor = PassiveBluetoothDataProcessor(sensor_update_to_bluetooth_data_update)
    entry.async_on_unload(
        processor.async_add_entities_listener(
            ThermoProBluetoothSensorEntity, async_add_entities
        )
    )
    entry.async_on_unload(
        coordinator.async_register_processor(processor, SensorEntityDescription)
    )


class ThermoProBluetoothSensorEntity(
    PassiveBluetoothProcessorEntity[
        PassiveBluetoothDataProcessor[float | int | None, SensorUpdate]
    ],
    SensorEntity,
):
    """Representation of a thermopro ble sensor."""

    @property
    @override
    def native_value(self) -> int | float | None:
        """Return the native value."""
        return self.processor.entity_data.get(self.entity_key)


class ThermoProActiveSensorEntity(
    CoordinatorEntity[ThermoProActiveCoordinator], SensorEntity
):
    """A sensor fed by a connected thermometer's broadcasts."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ThermoProActiveCoordinator, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        address = coordinator.address
        self._attr_unique_id = f"{address}-{key}"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, address)},
            connections={(dr.CONNECTION_BLUETOOTH, address)},
            name=coordinator.config_entry.title,
            manufacturer="ThermoPro",
            model=coordinator.model,
        )


class ThermoProProbeSensorEntity(ThermoProActiveSensorEntity):
    """Temperature of one probe."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1
    _attr_translation_key = "probe_temperature"

    def __init__(self, coordinator: ThermoProActiveCoordinator, probe: int) -> None:
        """Initialize the probe sensor."""
        super().__init__(coordinator, f"probe_{probe}")
        self._index = probe - 1
        self._attr_translation_placeholders = {"probe_number": str(probe)}

    @property
    @override
    def native_value(self) -> float | None:
        """Return the probe temperature; None while unplugged or settling."""
        if (data := self.coordinator.data) is None:
            return None
        return data.temperatures[self._index]


class ThermoProBatterySensorEntity(ThermoProActiveSensorEntity):
    """Battery of the thermometer's base unit."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ThermoProActiveCoordinator) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator, "battery")

    @property
    @override
    def native_value(self) -> int | None:
        """Return the battery level."""
        if (data := self.coordinator.data) is None:
            return None
        return data.battery
