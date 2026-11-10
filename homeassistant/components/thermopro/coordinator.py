"""Coordinator for ThermoPro thermometers that need a connection."""

import asyncio
from datetime import timedelta
import logging
from typing import override

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from thermopro_ble.tp902 import (
    FirmwareVersion,
    Notification,
    TemperatureBroadcast,
    TP902Session,
)

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothCallbackMatcher,
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Only used to reconnect. While connected, every broadcast (~1.5 s) pushes an
# update, which also pushes this timer back.
RECONNECT_INTERVAL = timedelta(seconds=30)


class ThermoProActiveCoordinator(DataUpdateCoordinator[TemperatureBroadcast | None]):
    """Keep a connection to a thermometer that only reports while connected.

    These models (TP902 family, TP25) put no readings in their advertisements.
    They advertise only while nobody is connected, so an advertisement is
    also the cue to reconnect.
    """

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, address: str, model: str
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"ThermoPro {model} {address}",
            update_interval=RECONNECT_INTERVAL,
        )
        self.address = address
        self.model = model
        self._session: TP902Session | None = None
        self._reader: asyncio.Task[None] | None = None

        entry.async_on_unload(
            bluetooth.async_register_callback(
                hass,
                self._async_handle_advertisement,
                BluetoothCallbackMatcher(address=address, connectable=True),
                BluetoothScanningMode.ACTIVE,
            )
        )

    @property
    def connected(self) -> bool:
        """Return whether a session is open."""
        return self._session is not None

    async def _connect(self, ble_device: BLEDevice) -> BleakClientWithServiceCache:
        """Open the connection, so the session tells us when it drops."""
        return await establish_connection(
            BleakClientWithServiceCache,
            ble_device,
            self.name,
            disconnected_callback=self._on_disconnected,
        )

    @override
    async def _async_update_data(self) -> TemperatureBroadcast | None:
        """Connect if needed; readings arrive through notifications."""
        if self._session is not None:
            return self.data

        ble_device = bluetooth.async_ble_device_from_address(
            self.hass, self.address, connectable=True
        )
        if ble_device is None:
            raise UpdateFailed("Thermometer is off or out of range")

        session = TP902Session(ble_device, connector=self._connect)
        try:
            await session.__aenter__()
        except (BleakError, TimeoutError) as err:
            raise UpdateFailed(f"Unable to connect: {err}") from err
        try:
            queue = await session.events()
            await session.authenticate()
            await session.request_firmware()
        except (BleakError, TimeoutError) as err:
            await session.__aexit__(None, None, None)
            raise UpdateFailed(f"Unable to start readings: {err}") from err

        self._session = session
        self._reader = self.config_entry.async_create_background_task(
            self.hass, self._async_read(queue), f"{self.name} reader"
        )
        return self.data

    async def _async_read(self, queue: asyncio.Queue[Notification]) -> None:
        """Hand each decoded notification to the entities."""
        while True:
            event = await queue.get()
            if isinstance(event, TemperatureBroadcast):
                self.async_set_updated_data(event)
            elif isinstance(event, FirmwareVersion):
                dr.async_get(self.hass).async_get_or_create(
                    config_entry_id=self.config_entry.entry_id,
                    identifiers={(DOMAIN, self.address)},
                    sw_version=f"{event.major}.{event.minor}",
                )

    @callback
    def _on_disconnected(self, _client: BleakClient) -> None:
        """Mark entities unavailable and try again soon."""
        if self._session is None:
            return
        _LOGGER.debug("%s disconnected", self.name)
        self.config_entry.async_create_background_task(
            self.hass, self._async_close(), f"{self.name} close"
        )
        self.async_set_update_error(UpdateFailed("Thermometer disconnected"))

    async def _async_close(self) -> None:
        """Stop reading and close the session, if one is open."""
        session, self._session = self._session, None
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.cancel()
        if session is not None:
            await session.__aexit__(None, None, None)

    @callback
    def _async_handle_advertisement(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        """Reconnect as soon as the thermometer shows up again."""
        if self._session is None:
            self._debounced_refresh.async_schedule_call()

    @override
    async def async_shutdown(self) -> None:
        """Disconnect when the entry unloads."""
        await super().async_shutdown()
        await self._async_close()
