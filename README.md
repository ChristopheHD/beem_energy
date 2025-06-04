# Beem Energy Home Assistant Integration

Custom integration to fetch and stream energy data from [Beem Energy](https://beem.energy) using their REST and MQTT APIs.

## ⚠️ Project Status: In Development

**This integration is currently under active development and is not fully functional yet.**  
Features may be incomplete or unstable, and breaking changes can occur at any time.  
We recommend waiting for a stable release before using this integration in a production environment.

## Features

- Live energy data streaming using MQTT.
- Secure login using your Beem Energy credentials.
- Simple setup via Home Assistant UI (Config Flow).

## Installation

1. Copy the `beem_energy` folder into your `config/custom_components` directory.
2. Restart Home Assistant.
3. Go to **Settings > Devices & Services > Add Integration**, search for "Beem Energy", and enter your Beem Energy email and password.

## Technical Details

- The integration authenticates to Beem Energy using the REST API, obtains MQTT tokens, then subscribes to the MQTT stream for live updates.
- Uses `paho-mqtt`, `requests`, and `pyjwt` (all installed automatically).

## To Do

- Support for multiple devices/accounts.
- Expose more sensor types (battery, grid, etc).
- Error handling improvements.
