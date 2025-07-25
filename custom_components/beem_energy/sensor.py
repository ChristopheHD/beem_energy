import logging
import requests
import json
import time
import asyncio
import ssl
from asyncio_mqtt import Client as AsyncMqttClient, MqttError


from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

BATTERY_SENSOR_KEYS = [
    "solar_power",
    "inverter_power",
    "battery_power",
    "grid_power",
    "soc",
    "mppt1_power",
    "mppt2_power",
    "mppt3_power",
]

def _get_beem_tokens_and_batteries(email, password):
    """Authenticate with Beem Energy and retrieve tokens and batteries data."""

    _LOGGER.debug("Starting authentication with Beem Energy for email: %s", email)

    try:
        response = requests.post(
            "https://api-x.beem.energy/beemapp/user/login",
            data={"email": email, "password": password},
            timeout=10,
        )
        _LOGGER.debug("Login response status: %s, body: %s", response.status_code, response.text)
        if response.status_code != 201:
            _LOGGER.error("Failed to get REST token: %s, response: %s", response.status_code, response.text)
            return None, None, []
        data = response.json()
        token_rest = data.get("accessToken")
        user_id = data.get("userId")
        client_id = f"beemapp-{user_id}-{round(time.time() * 1000)}"
    except Exception as e:
        _LOGGER.exception("Exception during login request: %s", e)
        return None, None, []

    try:
        response = requests.post(
            "https://api-x.beem.energy/beemapp/devices/mqtt/token",
            headers={"Authorization": f"Bearer {token_rest}"},
            data={"clientId": client_id, "clientType": "user"},
            timeout=10,
        )
        _LOGGER.debug("MQTT token response status: %s, body: %s", response.status_code, response.text)
        if response.status_code != 200:
            _LOGGER.error("Failed to get MQTT token: %s, response: %s", response.status_code, response.text)
            return None, None, []
        token_mqtt = response.json().get("jwt")
    except Exception as e:
        _LOGGER.exception("Exception during MQTT token request: %s", e)
        return None, None, []

    try:
        response = requests.get(
            "https://api-x.beem.energy/beemapp/devices",
            headers={"Authorization": f"Bearer {token_rest}"},
            data={"clientId": client_id, "clientType": "user"},
            timeout=10,
        )
        _LOGGER.debug("Devices response status: %s, body: %s", response.status_code, response.text)
        if response.status_code != 200:
            _LOGGER.error("Failed to get devices list: %s, response: %s", response.status_code, response.text)
            return None, None, []
        batteries = response.json().get("batteries", [])
        _LOGGER.info("Fetched %d batteries from Beem Energy.", len(batteries))
    except Exception as e:
        _LOGGER.exception("Exception during devices request: %s", e)
        return None, None, []

    return client_id, token_mqtt, batteries

class BeemEnergyMqttSensor(Entity):
    """Representation of a Beem Energy MQTT sensor."""

    def __init__(self, name, key, battery):
        """Initialize the sensor."""
        self._attr_name = name
        self._key = key
        self._attr_state = None
        self._attr_device_info = battery

        # Classes and units
        if key.lower().endswith("power"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_unit_of_measurement = "W"
        if key.lower() == "soc":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_unit_of_measurement = "%"

        self._attr_available = False

    def update_from_payload(self, payload):
        if self._key in payload:
            self._attr_state = payload[self._key]
            self._attr_available = True
        else:
            self._attr_available = False
        _LOGGER.debug("Updated sensor %s: %s", self._attr_name, self._attr_state)

async def start_mqtt_async(batteries, battery_sensors, client_id, token_mqtt):
    mqtt_server = "mqtt.beem.energy"
    mqtt_port = 8084

    ssl_context = ssl.create_default_context()

    async def handle_battery(serial_number, sensors):
        topic = f"battery/{serial_number}/sys/streaming"
        async with AsyncMqttClient(
            mqtt_server,
            port=mqtt_port,
            username=client_id,
            password=token_mqtt,
            tls_context=ssl_context,
            transport="websockets"
        ) as client:
            await client.subscribe(topic)
            async with client.unfiltered_messages() as messages:
                async for msg in messages:
                    try:
                        payload = json.loads(msg.payload.decode())
                        for sensor in sensors:
                            old = sensor.state
                            sensor.update_from_payload(payload)
                            sensor.schedule_update_ha_state()
                    except Exception as e:
                        _LOGGER.error("Error processing MQTT message: %s", e)

    tasks = [
        asyncio.create_task(handle_battery(battery["serialNumber"], battery_sensors[battery["serialNumber"]]))
        for battery in batteries
    ]
    await asyncio.gather(*tasks)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)

    # Appel synchrone pour récupérer les batteries
    client_id, token_mqtt, batteries = await hass.async_add_executor_job(
        _get_beem_tokens_and_batteries, email, password
    )
    if not all([client_id, token_mqtt]) or not batteries:
        _LOGGER.error("Could not retrieve necessary Beem Energy tokens or batteries.")
        return

    battery_sensors = {}
    entities = []

    for battery in batteries:
        device = DeviceInfo(
            identifiers={(DOMAIN, battery["serialNumber"])},
            name=f"Beem Battery {battery["id"]}",
            manufacturer="Beem Energy",
            sw_version=battery["firmwareVersion"],
            configuration_url="https://beem.energy",
            suggested_area=battery["location"],
        )
        sensors = []
        for key in BATTERY_SENSOR_KEYS:
            sensor_name = f"Beem Battery {battery.get("id")} {key.replace('_', ' ').title()}"
            sensor = BeemEnergyMqttSensor(
                name=sensor_name,
                key=key,
                battery=device
            )
            sensors.append(sensor)
            entities.append(sensor)
        battery_sensors[battery["serialNumber"]] = sensors

    # Stocke les batteries pour diagnostics
    hass.data.setdefault(DOMAIN, {})["batteries"] = batteries

    # Ajoute toutes les entités en une fois
    async_add_entities(entities, True)

    hass.loop.create_task(
        start_mqtt_async(batteries, battery_sensors, client_id, token_mqtt)
    )
