"""Config flow for thermopro ble integration."""

from typing import Any, override

from bluetooth_data_tools import short_address
import probatio
from thermopro_ble import ThermoProBluetoothDeviceData as DeviceData
from thermopro_ble.tp902 import supports as supports_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_MODEL

from .const import DOMAIN


def _connection_device(
    discovery_info: BluetoothServiceInfoBleak,
) -> tuple[str, dict[str, Any]] | None:
    """Return title and entry data for a model that reports over a connection.

    These advertise only their name, so there is nothing for DeviceData to
    parse; the model is stored so setup knows to connect.
    """
    name = discovery_info.name
    if not discovery_info.connectable or not supports_connection(name):
        return None
    model = name.split(" ", 1)[0]
    return f"{model} {short_address(discovery_info.address)}", {CONF_MODEL: model}


class ThermoProConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for thermopro."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_device: DeviceData | None = None
        self._discovered_devices: dict[str, str] = {}
        self._entry_data: dict[str, dict[str, Any]] = {}

    @override
    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        device = DeviceData()
        if connection := _connection_device(discovery_info):
            title, data = connection
            self._discovered_devices[discovery_info.address] = title
            self._entry_data[discovery_info.address] = data
        elif not device.supported(discovery_info):
            return self.async_abort(reason="not_supported")
        self._discovery_info = discovery_info
        self._discovered_device = device
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        assert self._discovered_device is not None
        device = self._discovered_device
        assert self._discovery_info is not None
        discovery_info = self._discovery_info
        address = discovery_info.address
        title = (
            self._discovered_devices.get(address)
            or device.title
            or device.get_device_name()
            or discovery_info.name
        )
        if user_input is not None:
            return self.async_create_entry(
                title=title, data=self._entry_data.get(address, {})
            )

        placeholders = {"name": title}
        self.context["title_placeholders"] = placeholders
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders=placeholders
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered_devices[address],
                data=self._entry_data.get(address, {}),
            )

        await bluetooth.async_request_active_scan(self.hass)
        current_addresses = self._async_current_ids(include_ignore=False)
        for discovery_info in async_discovered_service_info(self.hass, False):
            address = discovery_info.address
            if address in current_addresses or address in self._discovered_devices:
                continue
            if connection := _connection_device(discovery_info):
                title, data = connection
                self._discovered_devices[address] = title
                self._entry_data[address] = data
                continue
            device = DeviceData()
            if device.supported(discovery_info):
                self._discovered_devices[address] = (
                    device.title or device.get_device_name() or discovery_info.name
                )

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_ADDRESS): probatio.In(self._discovered_devices)}
            ),
        )
