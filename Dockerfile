ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.22
FROM ${BUILD_FROM}

WORKDIR /app
COPY smarthome /app/smarthome
COPY config/spotify_profiles.example.json /app/config/spotify_profiles.example.json
COPY config/spotify_targets.example.json /app/config/spotify_targets.example.json
COPY run.sh /run.sh
RUN pip install --no-cache-dir "keyring>=25,<26" \
    && chmod a+x /run.sh

CMD ["/run.sh"]
