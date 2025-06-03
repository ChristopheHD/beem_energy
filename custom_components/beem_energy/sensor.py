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

class BeemEnergySensor(Entity):
    def __init__(self, email, password):
        self._state = None
        self._email = email
        self._password = password
        self._attr_name = DEFAULT_NAME
        self._mqtt_thread = None
        self._stop_event = threading.Event()

    def start_mqtt(self):
        client_id, token_mqtt, serial_number = get_beem_tokens(self._email, self._password)
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
                self._state = payload.get("power", 0)
                _LOGGER.debug("Received Beem MQTT payload: %s", payload)
                self.schedule_update_ha_state()
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
                while not self._stop_event.is_set():
                    time.sleep(1)
                client.loop_stop()
            except Exception as e:
                _LOGGER.error("MQTT thread error: %s", e)

        self._mqtt_thread = threading.Thread(target=run, daemon=True)
        self._mqtt_thread.start()

    def stop_mqtt(self):
        if self._stop_event:
            self._stop_event.set()
        if self._mqtt_thread:
            self._mqtt_thread.join()

    def update(self):
        if not self._mqtt_thread:
            self.start_mqtt()

    @property
    def state(self):
        return self._state

    @property
    def available(self):
        return self._state is not None

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    email = entry.data.get(CONF_EMAIL)
    password = entry.data.get(CONF_PASSWORD)
    sensor = BeemEnergySensor(email, password)
    async_add_entities([sensor], True)