import logging
import requests
import json
import threading
import time
import paho.mqtt.client as mqtt

from homeassistant.helpers.entity import Entity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

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

def get_beem_tokens(email, password):
    # Authenticate via REST API
    response = requests.post(
        "https://api-x.beem.energy/beemapp/user/login",
        data={"email": email, "password": password},
        timeout=10,
    )
    if response.status_code != 201:
        _LOGGER.error("Failed to get REST token: %s", response.status_code)
        return None, None, None

    data = response.json()
    token_rest = data.get("accessToken")
    user_id = data.get("userId")
    client_id = f"beemapp-{user_id}-{round(time.time() * 1000)}"

    # Get MQTT token
    response = requests.post(
        "https://api-x.beem.energy/beemapp/devices/mqtt/token",
        headers={"Authorization": f"Bearer {token_rest}"},
        data={"clientId": client_id, "clientType": "user"},
        timeout=10,
    )
    if response.status_code != 200:
        _LOGGER.error("Failed to get MQTT token: %s", response.status_code)
        return None, None, None

    token_mqtt = response.json().get("jwt")

    # Get device serial number
    response = requests.get(
        "https://api-x.beem.energy/beemapp/devices",
        headers={"Authorization": f"Bearer {token_rest}"},
        data={"clientId": client_id, "clientType": "user"},
        timeout=10,
    )
    serial_number = response.json()["batteries"][0]["serialNumber"]

    return client_id, token_mqtt, serial_number

class BeemEnergyMqttSensor(Entity):
    def __init__(self, name, key):
        self._attr_name = name
        self._key = key
        self._state = None

        # Ajout automatique des classes pour les sensors de puissance
        if name.lower().endswith("power") or key.lower().endswith("power"):
            self._attr_device_class = SensorDeviceClass.POWER
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_unit_of_measurement = "W"
        if name.lower().endswith("soc") or key.lower().endswith("soc"):
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

def start_mqtt_and_update_sensors(sensors, email, password, stop_event):
    client_id, token_mqtt, serial_number = get_beem_tokens(email, password)
    if not all([client_id, token_mqtt, serial_number]):
        _LOGGER.error("Could not retrieve necessary Beem Energy tokens.")
        return

    mqtt_server = "mqtt.beem.energy"
    mqtt_port = 8084
    mqtt_topic = f"battery/{serial_number}/sys/streaming"

    def on_connect(client, userdata, flags, rc, prop=None):
        _LOGGER.info("Connected to Beem MQTT with result code %s", rc)
        client.subscribe(mqtt_topic)

    def on_message(client, userdata, msg):
        try:
            payload = json.loads(msg.payload)
            for sensor in sensors:
                sensor.update_from_payload(payload)
                sensor.schedule_update_ha_state()
            _LOGGER.debug("Received Beem MQTT payload: %s", payload)
        except Exception as e:
            _LOGGER.error("Error parsing Beem MQTT message: %s", e)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id, transport="websockets", protocol=mqtt.MQTTv5)
    client.username_pw_set(username=client_id, password=token_mqtt)
    client.tls_set()
    client.on_connect = on_connect
    client.on_message = on_message

    def run():
        try:
            client.connect(mqtt_server, mqtt_port, 60)
            client.loop_start()
            while not stop_event.is_set():
                time.sleep(1)
            client.loop_stop()
        except Exception as e:
            _LOGGER.error("MQTT thread error: %s", e)

    threading.Thread(target=run, daemon=True).start()

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    stop_event = threading.Event()

    sensors = [
        BeemEnergyMqttSensor(f"Beem {key.replace('_', ' ').title()}", key)
        for key in SENSOR_KEYS
    ]
    async_add_entities(sensors, True)
    threading.Thread(target=start_mqtt_and_update_sensors, args=(sensors, email, password, stop_event), daemon=True).start()
