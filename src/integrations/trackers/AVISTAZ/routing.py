from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import cli_ui

from src.domain_models.release import Meta
from src.integrations.observability.runtime_support import logger


def _routing_log(message: str) -> None:
    """Log Rich markup without applying a second highlighter pass."""
    logger.info(message, extra={"highlighter": None})


# These are the country groups used by the three tracker rule implementations.
PRIVATEHD_COUNTRIES = frozenset(
    [
        "AG",
        "AI",
        "AU",
        "BB",
        "BM",
        "BS",
        "BZ",
        "CA",
        "CW",
        "DM",
        "GB",
        "GD",
        "IE",
        "JM",
        "KN",
        "KY",
        "LC",
        "MS",
        "NZ",
        "PR",
        "TC",
        "TT",
        "US",
        "VC",
        "VG",
        "VI",
    ]
)
AVISTAZ_COUNTRIES = frozenset(
    [
        "BD",
        "BN",
        "BT",
        "CN",
        "HK",
        "ID",
        "IN",
        "JP",
        "KH",
        "KP",
        "KR",
        "LA",
        "LK",
        "MM",
        "MN",
        "MO",
        "MY",
        "NP",
        "PH",
        "PK",
        "SG",
        "TH",
        "TL",
        "TW",
        "VN",
    ]
)
ASIAN_COUNTRIES = frozenset(
    [
        "AE",
        "AF",
        "AM",
        "AZ",
        "BD",
        "BH",
        "BN",
        "BT",
        "CN",
        "CY",
        "GE",
        "HK",
        "ID",
        "IL",
        "IN",
        "IQ",
        "IR",
        "JO",
        "JP",
        "KG",
        "KH",
        "KP",
        "KR",
        "KW",
        "KZ",
        "LA",
        "LB",
        "LK",
        "MM",
        "MN",
        "MO",
        "MV",
        "MY",
        "NP",
        "OM",
        "PH",
        "PK",
        "PS",
        "QA",
        "SA",
        "SG",
        "SY",
        "TH",
        "TJ",
        "TL",
        "TM",
        "TR",
        "TW",
        "UZ",
        "VN",
        "YE",
    ]
)
CINEMAZ_COUNTRIES = frozenset(
    [
        "AO",
        "BF",
        "BI",
        "BJ",
        "BW",
        "CD",
        "CF",
        "CG",
        "CI",
        "CM",
        "CV",
        "DJ",
        "DZ",
        "EG",
        "EH",
        "ER",
        "ET",
        "GA",
        "GH",
        "GM",
        "GN",
        "GQ",
        "GW",
        "IO",
        "KE",
        "KM",
        "LR",
        "LS",
        "LY",
        "MA",
        "MG",
        "ML",
        "MR",
        "MU",
        "MW",
        "MZ",
        "NA",
        "NE",
        "NG",
        "RE",
        "RW",
        "SC",
        "SD",
        "SH",
        "SL",
        "SN",
        "SO",
        "SS",
        "ST",
        "SZ",
        "TD",
        "TF",
        "TG",
        "TN",
        "TZ",
        "UG",
        "YT",
        "ZA",
        "ZM",
        "ZW",
        "AR",
        "AW",
        "BL",
        "BO",
        "BQ",
        "BR",
        "CL",
        "CO",
        "CR",
        "CU",
        "DO",
        "EC",
        "FK",
        "GF",
        "GP",
        "GS",
        "GT",
        "GY",
        "HN",
        "HT",
        "MF",
        "MQ",
        "MX",
        "NI",
        "PA",
        "PE",
        "PM",
        "PY",
        "SR",
        "SV",
        "SX",
        "UY",
        "VE",
        "AD",
        "AL",
        "AT",
        "AX",
        "BA",
        "BE",
        "BG",
        "BY",
        "CH",
        "CZ",
        "DE",
        "DK",
        "EE",
        "ES",
        "FI",
        "FO",
        "FR",
        "GG",
        "GI",
        "GR",
        "HR",
        "HU",
        "IS",
        "IT",
        "JE",
        "LI",
        "LT",
        "LU",
        "LV",
        "MC",
        "MD",
        "ME",
        "MK",
        "MT",
        "NL",
        "NO",
        "PL",
        "PT",
        "RO",
        "RS",
        "RU",
        "SE",
        "SI",
        "SJ",
        "SK",
        "SM",
        "SU",
        "UA",
        "VA",
        "XC",
    ]
)


@dataclass(frozen=True)
class RoutingDecision:
    source: str
    destination: str | None
    reason: str
    automatic: bool


class AvistaZNetworkRouter:
    """Resolve unambiguous AvistaZ/CinemaZ/PrivateHD content routing."""

    network_trackers = frozenset({"AVISTAZ", "CINEMAZ", "PRIVATEHD"})

    def __init__(
        self, config: dict[str, Any], tracker_class_map: dict[str, Any]
    ):
        """Store configuration and tracker factories used for redirect validation."""
        self.config = config
        self.tracker_class_map = tracker_class_map

    @staticmethod
    def _countries(meta: Meta) -> set[str]:
        """Return normalized production-country ISO codes from metadata."""
        raw_countries = (
            meta.origin_country
            if isinstance(meta.origin_country, list)
            else []
        )
        return {str(country).upper() for country in raw_countries if country}

    @staticmethod
    def _is_older_than_50_years(meta: Meta) -> bool:
        """Determine whether the release year is at least fifty years old."""
        try:
            return datetime.now(UTC).year - int(meta.year or 0) >= 50
        except TypeError, ValueError:
            return False

    @staticmethod
    def _is_sd(meta: Meta) -> bool:
        """Identify SD content from either the explicit flag or its resolution."""
        if bool(meta.sd):
            return True
        resolution_match = re.search(r"(\d{3,4})", str(meta.resolution or ""))
        return bool(resolution_match and int(resolution_match.group(1)) < 720)

    @staticmethod
    def _config_enabled(value: Any) -> bool:
        """Interpret boolean configuration values without treating non-empty strings as true."""
        return (
            value
            if isinstance(value, bool)
            else str(value).strip().lower() in {"1", "true", "yes", "on"}
        )

    def _privatehd_destinations(
        self, meta: Meta, countries: set[str]
    ) -> list[tuple[str, str]]:
        destinations: list[tuple[str, str]] = []
        if self._is_older_than_50_years(meta):
            destinations.append(("CINEMAZ", "content is 50+ years old"))
        if countries & ASIAN_COUNTRIES:
            destinations.append(("AVISTAZ", "Asian production"))
        elif countries & CINEMAZ_COUNTRIES:
            destinations.append(
                ("CINEMAZ", "production belongs to CinemaZ's region")
            )
        return destinations

    @staticmethod
    def _cinemaz_destinations(countries: set[str]) -> list[tuple[str, str]]:
        if countries & AVISTAZ_COUNTRIES:
            return [("AVISTAZ", "Asian production")]
        return []

    def _recent_privatehd_candidate(
        self, meta: Meta, countries: set[str]
    ) -> bool:
        if not countries & PRIVATEHD_COUNTRIES:
            return False
        if self._is_older_than_50_years(meta):
            return False
        return not self._is_sd(meta)

    def _cinemaz_review_decision(
        self,
        source: str,
        meta: Meta,
        countries: set[str],
        destinations: list[tuple[str, str]],
    ) -> RoutingDecision | None:
        if destinations or not self._recent_privatehd_candidate(
            meta, countries
        ):
            return None
        return RoutingDecision(
            source,
            "PRIVATEHD",
            "recent HD content from a major English-speaking country may "
            "belong on PrivateHD",
            automatic=False,
        )

    @staticmethod
    def _avistaz_destinations(countries: set[str]) -> list[tuple[str, str]]:
        if countries & PRIVATEHD_COUNTRIES:
            return [
                (
                    "PRIVATEHD",
                    "production belongs to a major English-speaking country",
                )
            ]
        cinemaz_region = CINEMAZ_COUNTRIES | (
            ASIAN_COUNTRIES - AVISTAZ_COUNTRIES
        )
        if countries & cinemaz_region:
            return [("CINEMAZ", "production belongs to CinemaZ's region")]
        return []

    def _destinations_for_source(
        self, source: str, meta: Meta, countries: set[str]
    ) -> list[tuple[str, str]]:
        if source == "PRIVATEHD":
            return self._privatehd_destinations(meta, countries)
        if source == "CINEMAZ":
            return self._cinemaz_destinations(countries)
        if source == "AVISTAZ":
            return self._avistaz_destinations(countries)
        return []

    @staticmethod
    def _decision_from_destinations(
        source: str,
        destinations: list[tuple[str, str]],
    ) -> RoutingDecision | None:
        if not destinations:
            return None
        target_names = {target for target, _reason in destinations}
        if len(target_names) != 1:
            reasons = "; ".join(reason for _target, reason in destinations)
            return RoutingDecision(
                source,
                None,
                f"conflicting routing rules: {reasons}",
                automatic=False,
            )
        destination, reason = destinations[0]
        return RoutingDecision(source, destination, reason, automatic=True)

    def decide(self, source: str, meta: Meta) -> RoutingDecision | None:
        """Return redirect or review routing decision for one source tracker."""
        source = source.upper()
        countries = self._countries(meta)
        destinations = self._destinations_for_source(source, meta, countries)
        if source == "CINEMAZ":
            review = self._cinemaz_review_decision(
                source, meta, countries, destinations
            )
            if review is not None:
                return review
        return self._decision_from_destinations(source, destinations)

    @staticmethod
    def _record_review(
        source: str,
        decision: RoutingDecision,
        source_status: dict[str, Any],
    ) -> None:
        source_status["routing_suggested_to"] = decision.destination
        _routing_log(
            f"{source}: [yellow]Routing requires review: "
            f"{decision.reason}[/yellow]"
        )

    def _unattended_redirect_approved(
        self,
        source: str,
        destination: str,
        decision: RoutingDecision,
        source_status: dict[str, Any],
    ) -> bool:
        enabled = self._config_enabled(
            self.config.get("DEFAULT", {}).get(
                "avistaz_network_auto_redirect", False
            )
        )
        if enabled:
            return True
        source_status["routing_suggested_to"] = destination
        _routing_log(
            f"{source}: [yellow]Suggested redirect to {destination}: "
            f"{decision.reason}. Set avistaz_network_auto_redirect=true "
            "to enable this in unattended mode.[/yellow]"
        )
        return False

    @staticmethod
    def _attended_redirect_approved(
        source: str,
        destination: str,
        decision: RoutingDecision,
        source_status: dict[str, Any],
    ) -> bool:
        prompt = (
            f"{source}: {decision.reason}. Redirect this upload to "
            f"{destination}?"
        )
        approved = cli_ui.ask_yes_no(prompt, default=True)
        if not approved:
            source_status["routing_suggested_to"] = destination
        return bool(approved)

    def _redirect_approved(
        self,
        meta: Meta,
        source: str,
        destination: str,
        decision: RoutingDecision,
        source_status: dict[str, Any],
    ) -> bool:
        if meta.unattended:
            return self._unattended_redirect_approved(
                source, destination, decision, source_status
            )
        return self._attended_redirect_approved(
            source, destination, decision, source_status
        )

    async def _destination_credentials_valid(
        self,
        meta: Meta,
        source: str,
        destination: str,
        source_status: dict[str, Any],
    ) -> bool:
        destination_class = self.tracker_class_map.get(destination)
        if destination_class is None:
            source_status["routing_error"] = (
                f"Destination {destination} is not available."
            )
            return False
        try:
            destination_tracker = destination_class(config=self.config)
            valid = await destination_tracker.validate_credentials(meta)
        except Exception as exc:
            source_status["routing_error"] = (
                f"Could not validate {destination} credentials: {exc}"
            )
            _routing_log(
                f"{source}: [yellow]Not redirecting to {destination}: "
                "credential validation failed.[/yellow]"
            )
            return False
        if valid:
            return True
        source_status["routing_error"] = (
            f"Destination {destination} has no valid cookie session."
        )
        _routing_log(
            f"{source}: [yellow]Not redirecting to {destination}: "
            "cookie validation failed.[/yellow]"
        )
        return False

    @staticmethod
    def _redirected_trackers(
        trackers: list[str], source: str, destination: str
    ) -> list[str]:
        redirected = [tracker for tracker in trackers if tracker != source]
        if destination not in redirected:
            redirected.append(destination)
        return redirected

    @staticmethod
    def _record_redirect(
        meta: Meta,
        source: str,
        destination: str,
        decision: RoutingDecision,
        source_status: dict[str, Any],
    ) -> None:
        source_status.update(
            {
                "upload": False,
                "skipped": True,
                "redirected_to": destination,
                "status_message": (
                    f"Redirected to {destination}: {decision.reason}"
                ),
            }
        )
        destination_status = meta.tracker_status.setdefault(destination, {})
        destination_status.setdefault("redirected_from", []).append(source)
        _routing_log(
            f"{source}: [green]Redirected to {destination}: "
            f"{decision.reason}.[/green]"
        )

    async def _apply_decision(
        self,
        meta: Meta,
        trackers: list[str],
        source: str,
        decision: RoutingDecision,
    ) -> list[str]:
        source_status = meta.tracker_status.setdefault(source, {})
        source_status["routing_reason"] = decision.reason
        if not decision.automatic or not decision.destination:
            self._record_review(source, decision, source_status)
            return trackers
        destination = decision.destination
        if not self._redirect_approved(
            meta, source, destination, decision, source_status
        ):
            return trackers
        if not await self._destination_credentials_valid(
            meta, source, destination, source_status
        ):
            return trackers
        self._record_redirect(
            meta, source, destination, decision, source_status
        )
        return self._redirected_trackers(trackers, source, destination)

    async def apply(self, meta: Meta) -> None:
        """Apply confirmed, cookie-validated redirects to the tracker upload list."""
        trackers = [str(tracker).upper() for tracker in meta.trackers]
        for source in tuple(trackers):
            if source not in self.network_trackers:
                continue
            decision = self.decide(source, meta)
            if decision is not None:
                trackers = await self._apply_decision(
                    meta, trackers, source, decision
                )
        meta.trackers = trackers
