"""Pure, side-effect-free household presence and mode decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Presence(StrEnum):
    """Observed presence of one tracked household member."""

    HOME = "home"
    AWAY = "away"
    UNKNOWN = "unknown"


class HomeMode(StrEnum):
    """Mutually exclusive operating mode selected for the home."""

    PARTY = "party"
    KILIAN_VISIT = "kilian_visit"
    HOME = "home"
    UNCERTAIN = "uncertain"
    AWAY = "away"


@dataclass(frozen=True, slots=True)
class HouseholdContext:
    """Inputs used to select a home mode without controlling any device."""

    andreas: Presence = Presence.UNKNOWN
    erlene: Presence = Presence.UNKNOWN
    felipe: Presence = Presence.UNKNOWN
    kilian_calendar_active: bool = False
    party_mode: bool = False

    def __post_init__(self) -> None:
        for name in ("andreas", "erlene", "felipe"):
            if not isinstance(getattr(self, name), Presence):
                raise ValueError(f"Ungueltiger Anwesenheitsstatus fuer {name}.")
        if not isinstance(self.kilian_calendar_active, bool):
            raise ValueError("Der Kilian-Kalenderstatus muss boolesch sein.")
        if not isinstance(self.party_mode, bool):
            raise ValueError("Der Partymodus muss boolesch sein.")

    @property
    def people_home(self) -> tuple[str, ...]:
        """Return tracked household members currently reported at home."""

        people = (
            ("andreas", self.andreas),
            ("erlene", self.erlene),
            ("felipe", self.felipe),
        )
        return tuple(name for name, status in people if status is Presence.HOME)


def select_home_mode(context: HouseholdContext) -> HomeMode:
    """Select a conservative mode from presence, calendar, and override inputs.

    Manual party mode wins. An active Kilian calendar window is treated as an
    occupied guest period. Unknown phone states never produce away mode.
    """

    if context.party_mode:
        return HomeMode.PARTY
    if context.kilian_calendar_active:
        return HomeMode.KILIAN_VISIT
    if context.people_home:
        return HomeMode.HOME

    states = (context.andreas, context.erlene, context.felipe)
    if any(status is Presence.UNKNOWN for status in states):
        return HomeMode.UNCERTAIN
    return HomeMode.AWAY
