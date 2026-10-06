"""Configuration structures and YAML parsing logic for CheckRoyalCaribbeanPrice.

Provides typed dataclasses for application settings, account credentials,
watchlists, and casino offers, alongside YAML configuration loading with
environment variable substitution.
"""

from __future__ import annotations

import json
import os
import re
import yaml

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from royal_caribbean.utils.logging import RESET, YELLOW, log, setup_hybrid_logging

# Optional apprise dependency handling
try:
    from apprise import Apprise, NotifyFormat, __version__ as apprise_version
except ImportError:
    Apprise = None
    NotifyFormat = None
    apprise_version = "not installed"

# Optional curl_cffi dependency handling
try:
    from curl_cffi import requests
    IMPERSONATE_ARGS = {"impersonate": "chrome"}
except ImportError:
    import requests as plain_requests
    requests = plain_requests
    IMPERSONATE_ARGS = {}


# ==============================================================================
# Coercion Helpers
# ==============================================================================
def _config_bool(val: Any, default: bool = False) -> bool:
    """Coerces YAML input to boolean safely."""
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes", "on")
    return default


def _config_amount(val: Any, field_name: str) -> Optional[float]:
    """Coerces numeric configuration amounts, returning None when unset."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        raise ValueError(f"Configuration option '{field_name}' must be a valid number.")


def _config_days(val: Any, field_name: str, default: int = 14) -> int:
    """Coerces day counts to integers."""
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        raise ValueError(f"Configuration option '{field_name}' must be an integer.")


def _config_id_list(val: Any, field_name: str) -> List[str]:
    """Normalizes configuration lists of IDs into string representations."""
    if val is None:
        return []
    if isinstance(val, (str, int)):
        return [str(val)]
    if isinstance(val, list):
        return [str(item) for item in val]
    raise ValueError(f"Configuration option '{field_name}' must be a list or scalar ID.")


# ==============================================================================
# Notification Utilities
# ==============================================================================
def build_apprise(urls: List[Dict[str, Any]] | List[str]) -> Optional[Any]:
    """Constructs and configures an Apprise notification instance.

    Args:
        urls: List of URL strings or dictionary items containing a 'url' key.

    Returns:
        Configured Apprise instance or None if not installed or unconfigured.
    """
    if Apprise is None:
        if urls:
            log(YELLOW + "Apprise configuration found, but 'apprise' module is not installed." + RESET)
        return None

    if not urls:
        return None

    apobj = Apprise()
    for item in urls:
        url_str = item["url"] if isinstance(item, dict) and "url" in item else item
        if isinstance(url_str, str) and url_str.strip():
            apobj.add(url_str)

    return apobj if len(apobj) > 0 else None


# ==============================================================================
# Configuration Data Classes
# ==============================================================================
@dataclass
class APIAccess:
    """Authentication session container holding current digital passport tokens.

    Maintains the server-assigned user 'id', OAuth bearer token strings, and the
    persistent network connection session pool context.
    """

    token: str
    id: str
    session: Any
    loyalty_number: Optional[str] = None


@dataclass
class CasinoOffer:
    """A single Club Royale casino offer parsed from the guest offers API.

    Captures the bookable-offer essentials a player tracks: the redemption code,
    the offer type, the reserve-by deadline, and any FreePlay/perk sweeteners.
    """

    offer_code: str
    name: str
    offer_type_code: str
    offer_type_name: str
    reserve_by_date: Optional[str]
    campaign_name: str
    status: str
    perks: List[str] = field(default_factory=list)

    @classmethod
    def from_api(cls, raw: Dict[str, Any]) -> CasinoOffer:
        """Builds a CasinoOffer from one element of the API 'offers' array."""
        offer = raw.get("campaignOffer") or {}
        offer_type = offer.get("offerType") or {}
        perks = [
            p.get("perkName", "")
            for p in (offer.get("perkCodes") or [])
            if p.get("perkName")
        ]
        return cls(
            offer_code=offer.get("offerCode", "?"),
            name=offer.get("name") or raw.get("campaignName", ""),
            offer_type_code=offer_type.get("code", ""),
            offer_type_name=offer_type.get("name", ""),
            reserve_by_date=offer.get("reserveByDate"),
            campaign_name=raw.get("campaignName", ""),
            status=offer.get("status") or raw.get("status", ""),
            perks=perks,
        )

    @property
    def is_complimentary(self) -> bool:
        """True for a Complimentary (COMP) offer."""
        return self.offer_type_code == "COMP"

    def days_until_reserve_by(self) -> Optional[int]:
        """Whole days from now until the offer's reserve-by deadline."""
        if not self.reserve_by_date:
            return None
        try:
            date_str = self.reserve_by_date.replace("Z", "+00:00")
            deadline = datetime.fromisoformat(date_str)
            return (deadline - datetime.now(timezone.utc)).days
        except (ValueError, TypeError):
            return None


@dataclass
class PriceAlertExclusion:
    """Mute one product's price notifications within a reservation."""

    reservation: str
    prefix: str
    product: str
    guest: Optional[str] = None

    def matches(self, reservation: Any, ctx: Any) -> bool:
        """Checks if a given item context matches this suppression rule."""
        return (
            self.reservation == str(reservation)
            and self.prefix == str(ctx.prefix)
            and self.product == str(ctx.product)
            and (self.guest is None or self.guest == str(getattr(ctx, "passenger_ID", "")))
        )


@dataclass
class AccountInfo:
    """User credential profile used to initialize authenticated client sessions."""

    username: str
    password: str
    state: Optional[str] = None
    senior: bool = False
    military: bool = False
    fire: bool = False
    police: bool = False
    cruise_line: Optional[str] = "royalcaribbean"
    casino_offers_only: bool = False
    access: Optional[APIAccess] = None
    found_items: Set[str] = field(default_factory=set)
    loyalty_tier: Optional[str] = None
    loyalty_points: Optional[int] = None
    apobj: Optional[Any] = None

    @property
    def is_royal(self) -> bool:
        """True if target brand is Royal Caribbean."""
        return self.cruise_line.lower() in ("royal", "royalcaribbean", "royal caribbean", "r")

    @property
    def is_celebrity(self) -> bool:
        """True if target brand is Celebrity Cruises."""
        return self.cruise_line.lower() in ("celebrity", "celebritycruises", "celebrity cruises", "c")

    @property
    def api_brand(self) -> str:
        """Internal API brand string ('royal' or 'celebrity')."""
        return "celebrity" if self.is_celebrity else "royal"

    @property
    def url_brand(self) -> str:
        """Brand path segment used in web endpoints and OAuth portals."""
        return "celebritycruises" if self.is_celebrity else "royalcaribbean"

    @property
    def friendly_name(self) -> str:
        """Presentation label for logging and user notifications."""
        return "Celebrity Cruises" if self.is_celebrity else "Royal Caribbean"


@dataclass
class WatchListItem:
    """User-configured catalog item monitored for price fluctuations."""

    name: str
    prefix: str
    product: str
    price: float
    enabled: bool = True
    guest_age_string: str = "adult"
    reservations: Optional[List[str]] = field(default_factory=list)


@dataclass
class ProspectiveCruise:
    """An unbooked, prospective voyage monitored for price drops."""

    cruise_URL: str
    paid_price: float
    loyalty_number: Optional[str] = None
    notification_mode: str = "price"


@dataclass
class CruiseAppConfig:
    """Master configuration repository storing global application run state."""

    date_display_format: Optional[str] = "%x"
    request_timeout: int = REQUEST_TIMEOUT
    log_file: Optional[str] = None
    history_db: Optional[str] = None
    check_for_upgrades: bool = False
    upgrade_alert_below: Optional[float] = None
    upgrade_reservations: List[str] = field(default_factory=list)
    upgrade_sister_categories: bool = True
    cabin_availability_state_file: str = "data/cabin-availability.json"
    check_casino_offers: bool = False
    casino_offer_warn_days: int = 14
    availability: Optional[Any] = None
    output_watch_as_json: bool = False
    output_json_watch_file: Optional[str] = "output-json-watch.txt"
    apprise_urls: List[str] = field(default_factory=list)
    notify_on_error: bool = False
    apprise_test: Optional[bool] = None
    display_cruise_prices: bool = True
    minimum_saving_alert: Optional[float] = None
    show_promos: bool = False
    accounts: List[AccountInfo] = field(default_factory=list)
    watch_list: List[WatchListItem] = field(default_factory=list)
    ignored_price_alerts: List[PriceAlertExclusion] = field(default_factory=list)
    prospective_cruises: List[ProspectiveCruise] = field(default_factory=list)
    reservation_prices: Dict[str, float] = field(default_factory=dict)
    reservation_names: Dict[str, str] = field(default_factory=dict)
    paid_reservations: Set[str] = field(default_factory=set)
    apobj: Optional[Any] = None

    def __str__(self) -> str:
        """Pretty-prints configuration state as formatted JSON."""
        try:
            return json.dumps(asdict(self), indent=4, default=str)
        except Exception as e:
            return f"<CruiseAppConfig Error formatting: {e}>"

    def format_date(self, date_str: str) -> str:
        """Transforms a YYYYMMDD timestamp string into the user's preferred layout."""
        if not date_str:
            return ""
        clean_str = date_str.replace("-", "").replace("/", "")
        try:
            return datetime.strptime(clean_str, "%Y%m%d").strftime(self.date_display_format or "%x")
        except ValueError:
            return str(date_str)


# ==============================================================================
# Configuration Loaders & Environment Expansion
# ==============================================================================
def expand_env_vars(value: Any) -> Any:
    """Recursively substitutes whole ${VAR_NAME} placeholders with environment variables."""
    if isinstance(value, dict):
        return {k: expand_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_env_vars(v) for v in value]
    if isinstance(value, str):
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", value)
        if match and match.group(1) in os.environ:
            return os.environ[match.group(1)]
    return value


def load_config_file(config_path: str) -> Dict[str, Any]:
    """Parses a YAML configuration file with UTF-8 primary and system fallback decoding."""
    try:
        with open(config_path, "r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)
    except UnicodeDecodeError:
        with open(config_path, "r") as file:
            raw_data = yaml.safe_load(file)

    return expand_env_vars(raw_data or {})


def load_config_objects(config_path: str) -> CruiseAppConfig:
    """Loads, sanitizes, and maps YAML parameters into a structured CruiseAppConfig."""
    data = load_config_file(config_path)

    currency_present = "currency" in str(data)
    currency_override_present = "currencyOverride" in data

    if "availability" in data:
        raise ValueError("Rename availability to reservationAlerts in config.yaml")

    # Parse accounts
    accounts = [
        AccountInfo(
            username=a["username"],
            password=a["password"],
            state=a.get("state"),
            senior=a.get("senior", False),
            military=a.get("military", False),
            fire=a.get("fire", False),
            police=a.get("police", False),
            cruise_line=a.get("cruiseLine", "royalcaribbean"),
            casino_offers_only=_config_bool(a.get("casinoOffersOnly"), False),
            apobj=build_apprise(a.get("apprise") or []),
        )
        for a in (data.get("accountInfo") or [])
    ]

    # Parse prospective cruises
    prospective_cruises = []
    cruise_entries = data.get("cruises") or []
    if not isinstance(cruise_entries, list):
        raise ValueError("cruises must be a list")

    for index, item in enumerate(cruise_entries):
        mode = item.get("notificationMode", "price")
        if mode not in ("price", "availability"):
            raise ValueError(f"cruises[{index}].notificationMode must be price or availability")
        prospective_cruises.append(
            ProspectiveCruise(
                cruise_URL=item["cruiseURL"],
                paid_price=float(item.get("paidPrice", 0) if mode == "availability" else item["paidPrice"]),
                loyalty_number=item.get("loyaltyNumber"),
                notification_mode=mode,
            )
        )

    cabin_state_file = data.get("cabinAvailabilityStateFile", "data/cabin-availability.json")
    if not isinstance(cabin_state_file, str) or not cabin_state_file.strip():
        raise ValueError("cabinAvailabilityStateFile must be a nonempty file path")

    # Parse watch list
    watch_list = []
    for w in data.get("watchList") or []:
        item_kwargs = {
            "name": w["name"],
            "prefix": w["prefix"],
            "product": w["product"],
            "price": float(w["price"]),
        }
        if "enabled" in w:
            item_kwargs["enabled"] = w["enabled"]
        if "guestAgeString" in w:
            item_kwargs["guest_age_string"] = w["guestAgeString"]
        if "reservations" in w:
            item_kwargs["reservations"] = w["reservations"]

        watch_list.append(WatchListItem(**item_kwargs))

    apprise_urls = [item["url"] for item in (data.get("apprise") or []) if isinstance(item, dict) and "url" in item]
    apobj = build_apprise(data.get("apprise") or [])

    raw_alert = data.get("minimumSavingAlert", None)
    minimum_saving_alert = float(raw_alert) if raw_alert is not None else None

    config = CruiseAppConfig(
        display_cruise_prices=data.get("displayCruisePrices", True),
        minimum_saving_alert=minimum_saving_alert,
        notify_on_error=data.get("notifyOnError", False),
        show_promos=data.get("showPromos", False),
        request_timeout=int(data.get("requestTimeout", REQUEST_TIMEOUT)),
        date_display_format=data.get("dateDisplayFormat", "%x"),
        log_file=data.get("logFile"),
        history_db=data.get("historyDb"),
        check_for_upgrades=_config_bool(data.get("checkForUpgrades"), False),
        upgrade_alert_below=_config_amount(data.get("upgradeAlertBelow"), "upgradeAlertBelow"),
        upgrade_reservations=_config_id_list(data.get("upgradeReservations"), "upgradeReservations"),
        upgrade_sister_categories=_config_bool(data.get("upgradeSisterCategories"), True),
        cabin_availability_state_file=cabin_state_file,
        check_casino_offers=_config_bool(data.get("checkCasinoOffers"), False),
        casino_offer_warn_days=_config_days(data.get("casinoOfferWarnDays"), "casinoOfferWarnDays", 14),
        output_watch_as_json=data.get("outputWatchAsJson", False),
        output_json_watch_file=data.get("outputJsonFile", "output-json-watch.txt"),
        apobj=apobj,
        accounts=accounts,
        watch_list=watch_list,
        prospective_cruises=prospective_cruises,
        apprise_urls=apprise_urls,
        reservation_prices=data.get("reservationPricePaid", {}),
        reservation_names=data.get("reservationFriendlyNames", {}),
        apprise_test=data.get("appriseTest", data.get("apprise_test", False)),
        paid_reservations={str(r) for r in (data.get("reservationsPaidInFull") or [])},
    )

    setup_hybrid_logging(config.log_file)

    if currency_override_present:
        log(YELLOW + "Due to RCCL API updates, config file option 'currencyOverride' is deprecated" + RESET)
    if currency_present:
        log(YELLOW + "Due to RCCL API updates, config file watchlist option 'currency' is deprecated" + RESET)

    return config
