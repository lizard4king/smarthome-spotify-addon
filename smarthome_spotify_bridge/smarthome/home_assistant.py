"""Restrictive Home Assistant REST adapter with writes disabled by default."""

from __future__ import annotations

import ipaddress
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from smarthome.models import validate_temperature


DEFAULT_TIMEOUT_SECONDS = 5.0
LIGHT_IDS = frozenset({"wohnzimmerlicht", "schlafzimmerlicht"})
ENTITY_PATTERN = re.compile(r"^(light|climate)\.[a-z0-9_]+$")


class HomeAssistantError(ValueError):
    """Base error for safe Home Assistant failures."""


class HomeAssistantConfigurationError(HomeAssistantError):
    """Raised when local configuration is missing or unsafe."""


class HomeAssistantWriteBlocked(HomeAssistantError):
    """Raised before a write when explicit write permission is absent."""


class HomeAssistantConnectionError(HomeAssistantError):
    """Raised for controlled HTTP, network, or response errors."""


class JsonTransport(Protocol):
    """Small injectable HTTP boundary used by the adapter."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> Any:
        """Send one JSON request and return decoded JSON."""


class UrllibJsonTransport:
    """Standard-library JSON transport that never includes secrets in errors."""

    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any] | None,
        timeout: float,
    ) -> Any:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                status = response.status
                body = response.read().decode("utf-8")
        except HTTPError as exc:
            raise HomeAssistantConnectionError(
                f"Home Assistant antwortete mit HTTP {exc.code}."
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise HomeAssistantConnectionError(
                "Home Assistant ist nicht erreichbar. Es wurde keine Aktion bestätigt."
            ) from exc

        if status not in {200, 201}:
            raise HomeAssistantConnectionError(
                f"Home Assistant antwortete mit HTTP {status}."
            )
        try:
            return json.loads(body) if body else None
        except json.JSONDecodeError as exc:
            raise HomeAssistantConnectionError(
                "Home Assistant lieferte keine gültige JSON-Antwort."
            ) from exc


@dataclass(frozen=True, slots=True)
class HomeAssistantConfig:
    """Validated environment-backed Home Assistant configuration."""

    base_url: str
    token: str
    entities: dict[str, str]
    allow_writes: bool = False
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _validate_base_url(self.base_url))
        if not self.token.strip():
            raise HomeAssistantConfigurationError(
                "HOME_ASSISTANT_TOKEN ist nicht gesetzt."
            )
        if not 0 < self.timeout_seconds <= 30:
            raise HomeAssistantConfigurationError(
                "Das Home-Assistant-Zeitlimit muss zwischen 0 und 30 Sekunden liegen."
            )

        required = {
            "wohnzimmerlicht": "light",
            "schlafzimmerlicht": "light",
            "wohnzimmerheizung": "climate",
        }
        if set(self.entities) != set(required):
            raise HomeAssistantConfigurationError(
                "Die Home-Assistant-Entity-Zuordnung ist unvollständig."
            )
        for device_id, domain in required.items():
            entity_id = self.entities[device_id]
            if not isinstance(entity_id, str) or not ENTITY_PATTERN.fullmatch(entity_id):
                raise HomeAssistantConfigurationError(
                    f"Ungültige Entity-ID für {device_id}."
                )
            if not entity_id.startswith(f"{domain}."):
                raise HomeAssistantConfigurationError(
                    f"Falsche Entity-Domäne für {device_id}."
                )

    @classmethod
    def from_environment(cls) -> HomeAssistantConfig:
        """Read configuration only from process environment variables."""

        return cls(
            base_url=os.getenv("HOME_ASSISTANT_URL", ""),
            token=os.getenv("HOME_ASSISTANT_TOKEN", ""),
            entities={
                "wohnzimmerlicht": os.getenv(
                    "HOME_ASSISTANT_WOHNZIMMERLICHT", ""
                ),
                "schlafzimmerlicht": os.getenv(
                    "HOME_ASSISTANT_SCHLAFZIMMERLICHT", ""
                ),
                "wohnzimmerheizung": os.getenv(
                    "HOME_ASSISTANT_WOHNZIMMERHEIZUNG", ""
                ),
            },
            allow_writes=os.getenv("HOME_ASSISTANT_ALLOW_WRITES", "").casefold()
            == "true",
        )


class HomeAssistantAdapter:
    """Call only the explicitly supported Home Assistant REST services."""

    def __init__(
        self,
        config: HomeAssistantConfig,
        transport: JsonTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibJsonTransport()

    def healthcheck(self) -> bool:
        """Perform a read-only API availability check."""

        response = self._request("GET", "api/", None)
        return isinstance(response, dict) and response.get("message") == "API running."

    def set_light(self, device_id: str, is_on: bool) -> None:
        """Call turn_on or turn_off for one mapped light."""

        self._require_writes()
        if device_id not in LIGHT_IDS or not isinstance(is_on, bool):
            raise HomeAssistantError("Der Home-Assistant-Lichtbefehl ist ungültig.")
        service = "turn_on" if is_on else "turn_off"
        self._request(
            "POST",
            f"api/services/light/{service}",
            {"entity_id": self.config.entities[device_id]},
        )

    def set_temperature(self, device_id: str, temperature: float) -> None:
        """Call climate.set_temperature for the mapped thermostat."""

        self._require_writes()
        if device_id != "wohnzimmerheizung":
            raise HomeAssistantError("Das Home-Assistant-Thermostat ist unbekannt.")
        target = validate_temperature(temperature)
        self._request(
            "POST",
            "api/services/climate/set_temperature",
            {
                "entity_id": self.config.entities[device_id],
                "temperature": target,
            },
        )

    def _require_writes(self) -> None:
        if not self.config.allow_writes:
            raise HomeAssistantWriteBlocked(
                "Home-Assistant-Schreibzugriffe sind deaktiviert. Setze "
                "HOME_ASSISTANT_ALLOW_WRITES erst nach ausdrücklicher Freigabe auf true."
            )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> Any:
        url = urljoin(f"{self.config.base_url}/", path)
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        return self.transport.request(
            method,
            url,
            headers,
            payload,
            self.config.timeout_seconds,
        )


def _validate_base_url(value: str) -> str:
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise HomeAssistantConfigurationError(
            "HOME_ASSISTANT_URL muss eine vollständige HTTP- oder HTTPS-URL sein."
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HomeAssistantConfigurationError(
            "HOME_ASSISTANT_URL darf keine Zugangsdaten, Query oder Fragment enthalten."
        )
    if parsed.path not in {"", "/"}:
        raise HomeAssistantConfigurationError(
            "HOME_ASSISTANT_URL darf keinen zusätzlichen Pfad enthalten."
        )

    if parsed.scheme == "http" and not _is_local_hostname(parsed.hostname):
        raise HomeAssistantConfigurationError(
            "Unverschlüsseltes HTTP ist nur für lokale Home-Assistant-Adressen erlaubt."
        )
    return url


def _is_local_hostname(hostname: str) -> bool:
    host = hostname.casefold()
    if host == "localhost" or host.endswith(".local"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback
