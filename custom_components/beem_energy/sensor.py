import logging
import requests
import json
import threading
import time
import paho.mqtt.client as mqtt

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from .const import DOMAIN, CONF_EMAIL, CONF_PASSWORD, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

SENSOR_KEYS = [
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
    import requests
    import time
    import logging

    _LOGGER = logging.getLogger(__name__)
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
    def __init__(self, name, key, serial_number, firmware_version, battery_data, device_id):
        self._attr_name = name
        self._key = key
        self._state = None
        self._serial_number = serial_number
        self._firmware_version = firmware_version
        self._device_id = device_id
        self._battery_data = battery_data

        # Classes and units
        if key.lower().endswith("power"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_unit_of_measurement = "W"
        if key.lower() == "soc":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_unit_of_measurement = "%"

    @property
    def state(self):
        return self._state

    def update_from_payload(self, payload):
        if self._key in payload:
            self._state = payload[self._key]

    @property
    def available(self):
        return self._state is not None

    @property
    def device_info(self):
        # Attach entity to the battery device
        info = {
            "identifiers": {(DOMAIN, self._serial_number)},
            "name": f"Beem Battery {self._serial_number}",
            "manufacturer": "Beem Energy",
            "model": f"Beem Battery ({self._firmware_version})",
            "sw_version": self._firmware_version,
        }
        # Add location if exists
        if self._battery_data.get("location"):
            info["suggested_area"] = self._battery_data["location"]
        return info

    @property
    def extra_state_attributes(self):
        # Diagnostic and battery info
        attrs = {}
        attrs["serial_number"] = self._serial_number
        attrs["firmware_version"] = self._firmware_version
        attrs["created_at"] = self._battery_data.get("createdAt")
        attrs["warranty_status"] = self._battery_data.get("warrantyStatus")
        attrs["location"] = self._battery_data.get("location")
        attrs["declared_number_of_modules"] = self._battery_data.get("declaredNumberOfModules")
        attrs["number_of_mppts"] = self._battery_data.get("numberOfMppts")
        attrs["user_access_mode"] = self._battery_data.get("userAccessMode")
        attrs["house_id"] = self._battery_data.get("houseId")
        attrs["is_installation_status"] = self._battery_data.get("isInstallationStatus")
        attrs["reversed_ct"] = self._battery_data.get("reversedCt")
        attrs["is_streaming_raw_grid_power"] = self._battery_data.get("isStreamingRawGridPower")
        attrs["use_ac_coupling"] = self._battery_data.get("useAcCoupling")
        attrs["live_data_smoothing_params"] = self._battery_data.get("liveDataSmoothingParams")
        attrs["battery_id"] = self._battery_data.get("id")
        # Add any additional fields as needed
        return attrs

def start_mqtt_and_update_sensors(battery_sensors, email, password, stop_event):
    client_id, token_mqtt, batteries = _get_beem_tokens_and_batteries(email, password)
    if not all([client_id, token_mqtt]) or not batteries:
        _LOGGER.error("Could not retrieve necessary Beem Energy tokens or batteries.")
        return

    mqtt_server = "mqtt.beem.energy"
    mqtt_port = 8084

    # For each battery, subscribe to its topic
    mqtt_clients = []
    for battery in batteries:
        serial_number = battery["serialNumber"]
        topic = f"battery/{serial_number}/sys/streaming"
        sensors = battery_sensors[serial_number]

        def make_on_message(sensors):
            def on_message(client, userdata, msg):
                try:
                    payload = json.loads(msg.payload)
                    for sensor in sensors:
                        sensor.update_from_payload(payload)
                        sensor.schedule_update_ha_state()
                    _LOGGER.debug("Received Beem MQTT payload for battery %s: %s", serial_number, payload)
                except Exception as e:
                    _LOGGER.error("Error parsing Beem MQTT message for battery %s: %s", serial_number, e)
            return on_message

        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id, transport="websockets", protocol=mqtt.MQTTv5)
        client.username_pw_set(username=client_id, password=token_mqtt)
        client.tls_set()
        client.on_connect = lambda client, userdata, flags, rc, prop=None: (
            _LOGGER.info("Connected to Beem MQTT (battery %s) with result code %s", serial_number, rc),
            client.subscribe(topic)
        )
        client.on_message = make_on_message(sensors)

        def run(client=client):
            try:
                client.connect(mqtt_server, mqtt_port, 60)
                client.loop_start()
                while not stop_event.is_set():
                    time.sleep(1)
                client.loop_stop()
            except Exception as e:
                _LOGGER.error("MQTT thread error for battery %s: %s", serial_number, e)

        threading.Thread(target=run, daemon=True).start()
        mqtt_clients.append(client)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback):
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    stop_event = threading.Event()
    # Get all batteries (call sync code in executor)
    client_id, token_mqtt, batteries = await hass.async_add_executor_job(
        _get_beem_tokens_and_batteries, email, password
    )
    if not batteries:
        _LOGGER.error("No batteries found for the Beem Energy account.")
        return
    battery_sensors = {}
    entities = []
    for battery in batteries:
        serial_number = battery["serialNumber"]
        firmware_version = battery.get("firmwareVersion", "unknown")
        device_id = battery.get("id")
        sensors = []
        for key in SENSOR_KEYS:
            sensor_name = f"Beem {serial_number} {key.replace('_', ' ').title()}"
            sensor = BeemEnergyMqttSensor(
                name=sensor_name,
                key=key,
                serial_number=serial_number,
                firmware_version=firmware_version,
                battery_data=battery,
                device_id=device_id,
            )
            sensors.append(sensor)
            entities.append(sensor)
        battery_sensors[serial_number] = sensors
    # Store batteries for diagnostics
    hass.data.setdefault(DOMAIN, {})["batteries"] = batteries
    async_add_entities(entities, True)
    threading.Thread(target=start_mqtt_and_update_sensors, args=(battery_sensors, email, password, stop_event), daemon=True).start()
