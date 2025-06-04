"""Diagnostics support for Beem Energy integration."""
from __future__ import annotations
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntry
from .const import DOMAIN, CONF_EMAIL

async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
):
    """Return diagnostics for a config entry (all batteries)."""
    batteries = hass.data[DOMAIN].get("batteries", [])
    return {
        "email": entry.data.get(CONF_EMAIL, ""),
        "battery_count": len(batteries),
        "batteries": [
            _battery_diag(battery)
            for battery in batteries
        ],
    }

async def async_get_device_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry, device: DeviceEntry
):
    """Return diagnostics for a specific device (battery)."""
    serial = None
    for ident in device.identifiers:
        if ident[0] == DOMAIN:
            serial = ident[1]
    batteries = hass.data[DOMAIN].get("batteries", [])
    for battery in batteries:
        if battery.get("serialNumber") == serial:
            return _battery_diag(battery)
    return {}

def _battery_diag(battery):
    """Extract relevant information for diagnostics."""
    return {
        "serial_number": battery.get("serialNumber"),
        "firmware_version": battery.get("firmwareVersion"),
        "created_at": battery.get("createdAt"),
        "warranty_status": battery.get("warrantyStatus"),
        "location": battery.get("location"),
        "declared_number_of_modules": battery.get("declaredNumberOfModules"),
        "number_of_mppts": battery.get("numberOfMppts"),
        "user_access_mode": battery.get("userAccessMode"),
        "house_id": battery.get("houseId"),
        "is_installation_status": battery.get("isInstallationStatus"),
        "reversed_ct": battery.get("reversedCt"),
        "is_streaming_raw_grid_power": battery.get("isStreamingRawGridPower"),
        "use_ac_coupling": battery.get("useAcCoupling"),
        "live_data_smoothing_params": battery.get("liveDataSmoothingParams"),
        "battery_id": battery.get("id"),
        # Ajoute ici d'autres champs pertinents si besoin
    }
