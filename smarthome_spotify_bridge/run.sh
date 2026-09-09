#!/usr/bin/with-contenv bashio
set -euo pipefail

if ! bashio::config.has_value 'bridge_secret' || ! bashio::config.has_value 'spotify_client_id'; then
  bashio::log.error 'bridge_secret und spotify_client_id müssen im Add-on gesetzt werden.'
  exit 1
fi

export SPOTIFY_BRIDGE_SECRET="$(bashio::config 'bridge_secret')"
export SPOTIFY_CLIENT_ID="$(bashio::config 'spotify_client_id')"
exec python3 /app/addon_entrypoint.py
