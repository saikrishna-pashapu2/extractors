import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse, urlunparse

import requests

from events.additional_sources import (
    cbuae_events,
    climate_bonds_events,
    oecd_events,
    wef_events,
)
from events.event_page_scraper import (
    DEFAULT_DENY_URL_PATTERNS,
    EventPageConfig,
    enrich_event_details,
    enrich_events,
    scrape_event_page,
)
from events.event_translator import translate_events_to_english


ADB_ESG_KEYWORDS = (
    "ESG",
    "Sustainability",
    "Sustainable",
    "Climate",
    "Net Zero",
    "Green",
    "Energy",
    "Environment",
    "Carbon",
    "Emissions",
    "Biodiversity",
    "Resilience",
    "Circular Economy",
    "Sustainable Finance",
    "Green Finance",
    "Responsible Investment",
    "Water",
    "SDG",
    "Decarbonization",
    "Transition",
    "Renewable",
)

GENERAL_ESG_KEYWORDS = tuple(
    dict.fromkeys(
        (
            *ADB_ESG_KEYWORDS,
            "Environmental",
            "Nature",
            "Natural Capital",
            "Methane",
            "Clean Energy",
            "Sustainable Aviation Fuel",
            "Responsible Business",
        )
    )
)

IATA_ESG_KEYWORDS = (
    "Sustainability",
    "Environment",
    "Sustainable Aviation Fuel",
    "SAF",
    "Net Zero",
    "Decarbonization",
    "Carbon",
    "Climate",
    "Energy Transition",
    "Emissions",
)

UAE_ESG_KEYWORDS = (
    "Sustainability",
    "Climate",
    "Green",
    "Environment",
    "Net Zero",
    "Energy",
    "Renewable",
    "ESG",
    "Circular Economy",
    "Biodiversity",
    "Carbon",
    "Sustainable Finance",
)

AIIB_EXCLUDED_KEYWORDS = (
    "Recruitment",
    "Career Fair",
    "Career Fairs",
    "Graduate Program",
    "Graduate Programs",
    "Internal Institutional Ceremony",
    "Internal Institutional Ceremonies",
)


@dataclass(frozen=True)
class RevisedPage:
    url: str
    include_keywords: Tuple[str, ...] = ()
    exclude_keywords: Tuple[str, ...] = ()
    allow_url_patterns: Tuple[str, ...] = ()
    deny_url_patterns: Tuple[str, ...] = ()


@dataclass(frozen=True)
class RevisedSourceConfig:
    key: str
    source: str
    pages: Tuple[RevisedPage, ...]
    comment: str = ""
    category: str = ""
    allowed_domains: Tuple[str, ...] = ()

    @property
    def url(self) -> str:
        return self.pages[0].url


def _page(
    url: str,
    include: Sequence[str] = (),
    exclude: Sequence[str] = (),
    allow_url_patterns: Sequence[str] = (),
    deny_url_patterns: Sequence[str] = (),
) -> RevisedPage:
    return RevisedPage(
        url=url,
        include_keywords=tuple(include),
        exclude_keywords=tuple(exclude),
        allow_url_patterns=tuple(allow_url_patterns),
        deny_url_patterns=tuple(deny_url_patterns),
    )


def _source(
    key: str,
    source: str,
    *pages: RevisedPage,
    comment: str = "",
    allowed_domains: Sequence[str] = (),
) -> RevisedSourceConfig:
    return RevisedSourceConfig(
        key=key,
        source=source,
        pages=tuple(pages),
        comment=comment,
        allowed_domains=tuple(allowed_domains),
    )


# Source pages and comments transcribed from Revised events links.xlsx.
# A malformed duplicated Net Zero Tracker URL was normalized to one valid URL.
REVISED_EVENT_SOURCES = (
    _source(
        "adb",
        "Asian Development Bank (ADB)",
        _page(
            "https://www.adb.org/news/events/calendar",
            include=ADB_ESG_KEYWORDS,
        ),
        comment="Include only events matching the workbook ESG keyword list.",
    ),
    _source(
        "aiib",
        "Asian Infrastructure Investment Bank (AIIB)",
        _page(
            "https://www.aiib.org/en/news-events/events/upcoming-events/index.html",
            exclude=AIIB_EXCLUDED_KEYWORDS,
            allow_url_patterns=(r"/news-events/events/",),
            deny_url_patterns=(
                r"/events/annual-meetings/overview/index\.html",
                r"/events/upcoming-events/index\.html",
                r"/events/past-events/index\.html",
            ),
        ),
        comment=(
            "Exclude recruitment, career fairs, graduate programs, and internal "
            "institutional ceremonies."
        ),
    ),
    _source("cdp", "CDP", _page("https://www.cdp.net/en/events")),
    _source(
        "cbuae",
        "Central Bank of the UAE (CBUAE)",
        _page("https://www.centralbank.ae/en/news-and-publications/events/"),
        comment="Keep even though the page has not been updated in recent years.",
    ),
    _source(
        "climate_bonds_initiative",
        "Climate Bonds Initiative",
        _page("https://www.climatebonds.net/news-events/events-training-webinars"),
    ),
    _source(
        "crrem_foundation",
        "CRREM Foundation",
        _page("https://crrem.org/news/#events"),
    ),
    _source(
        "emirates_gbc",
        "Emirates Green Building Council",
        _page("https://emiratesgbc.org/technical-workshops/"),
        _page("https://emiratesgbc.org/annual-emiratesgbc-congress/"),
        _page("https://emiratesgbc.org/awards/"),
        _page("https://emiratesgbc.org/sponsorship-opportunities/"),
        comment="Scrape workshops plus congress, awards, and sponsorship pages.",
    ),
    _source(
        "ebrd",
        "European Bank for Reconstruction and Development (EBRD)",
        _page(
            "https://www.ebrd.com/home/news-and-events.html",
            include=GENERAL_ESG_KEYWORDS,
        ),
        _page("https://www.ebrdgreencities.com/news-and-events/events"),
        comment=(
            "Filter the main hub for ESG topics and also scrape the dedicated "
            "Green Cities events page."
        ),
    ),
    _source(
        "european_commission",
        "European Union / European Commission",
        _page(
            "https://european-union.europa.eu/news-and-events/events_en",
            include=GENERAL_ESG_KEYWORDS,
            exclude=(
                "Council Meeting",
                "Budget Conference",
                "Parliament Session",
            ),
        ),
        _page("https://energy.ec.europa.eu/events_en"),
        _page("https://www.eea.europa.eu/en/newsroom/events"),
        comment=(
            "Filter the broad EU page and also scrape the energy and European "
            "Environment Agency event pages."
        ),
        allowed_domains=("europa.eu",),
    ),
    _source(
        "fsb",
        "Financial Stability Board (FSB)",
        _page("https://www.fsb.org/policy_areas/climate/"),
        comment="Use the revised climate policy page.",
    ),
    _source(
        "fao",
        "Food and Agriculture Organization (FAO)",
        _page("https://www.fao.org/climate-change/events/en"),
    ),
    _source(
        "gggi",
        "Global Green Growth Institute (GGGI)",
        _page("https://gggi.org/events/list/"),
    ),
    _source(
        "gh2",
        "Green Hydrogen Organisation (GH2)",
        _page("https://gh2.org/events"),
    ),
    _source(
        "ghg_protocol",
        "Greenhouse Gas Protocol",
        _page("https://ghgprotocol.org/events"),
    ),
    _source(
        "gresb",
        "GRESB",
        _page("https://www.gresb.com/gresb-industry-insights/events-calendar/"),
    ),
    _source(
        "ifrs_foundation",
        "IFRS Foundation",
        _page(
            "https://www.ifrs.org/news-and-events/calendar/",
            include=(
                "International Sustainability Standards Board",
                "ISSB",
                "Sustainability Conference",
                "Sustainability Conferences",
                "Sustainability Workshop",
                "Sustainability Workshops",
                "Sustainability Consultative Meeting",
                "Sustainability Consultative Meetings",
            ),
        ),
        comment="Keep only ISSB and sustainability-related calendar events.",
    ),
    _source(
        "ipcc",
        "Intergovernmental Panel on Climate Change (IPCC)",
        _page("https://www.ipcc.ch/calendar/"),
    ),
    _source(
        "iata",
        "International Air Transport Association (IATA)",
        _page("https://www.iata.org/en/events/", include=IATA_ESG_KEYWORDS),
        comment="Keep only events matching the workbook aviation ESG keywords.",
    ),
    _source(
        "icma",
        "International Capital Market Association (ICMA)",
        _page("https://www.icmagroup.org/events/"),
    ),
    _source(
        "icap",
        "International Carbon Action Partnership (ICAP)",
        _page("https://icapcarbonaction.com/en/events"),
    ),
    _source(
        "icmm",
        "International Council on Mining and Metals (ICMM)",
        _page("https://www.icmm.com/events"),
    ),
    _source(
        "iea",
        "International Energy Agency (IEA)",
        _page("https://www.iea.org/events"),
    ),
    _source(
        "ifc",
        "International Finance Corporation (IFC)",
        _page("https://www.ifc.org/en/news"),
    ),
    _source(
        "imo",
        "International Maritime Organization (IMO)",
        _page("https://www.imo.org/en/about/events/Pages/Default.aspx"),
        _page(
            "https://www.imo.org/en/ourwork/environment/pages/"
            "imo-climate-events.aspx"
        ),
        comment="Also scrape the IMO climate events page.",
    ),
    _source(
        "irena",
        "International Renewable Energy Agency (IRENA)",
        _page("https://www.irena.org/Events?orderBy=Date"),
        comment="Use the workbook URL ordered by most recent date.",
    ),
    _source(
        "ipieca",
        "IPIECA",
        _page("https://www.ipieca.org/insights/events"),
    ),
    _source(
        "lseg",
        "LSEG",
        _page("https://www.lseg.com/en/events", include=GENERAL_ESG_KEYWORDS),
        comment="Filter the broad event page for ESG topics.",
    ),
    _source(
        "ngfs",
        "Network for Greening the Financial System (NGFS)",
        _page(
            "https://www.ngfs.net/en/news?category%5B5412711%5D=5412711"
            "&end-date=&start-date="
        ),
        comment="Use the corrected filtered news/events link.",
    ),
    _source("oecd", "OECD", _page("https://www.oecd.org/en/events.html")),
    _source(
        "pri",
        "Principles for Responsible Investment (PRI)",
        _page("https://www.unpri.org/events"),
    ),
    _source(
        "carec",
        "Regional Environmental Centre for Central Asia (CAREC)",
        _page("https://carececo.org/en/main/activity/events/"),
    ),
    _source(
        "rmi",
        "Responsible Minerals Initiative (RMI)",
        _page("https://www.responsiblemineralsinitiative.org/events/"),
    ),
    _source(
        "saudi_green_initiative",
        "Saudi Green Initiative",
        _page("https://www.sgi.gov.sa/events/"),
    ),
    _source(
        "saudi_ministry_energy",
        "Saudi Ministry of Energy",
        _page("https://www.moenergy.gov.sa/en/media-center/events"),
    ),
    _source(
        "sbti",
        "Science Based Targets initiative (SBTi)",
        _page("https://sciencebasedtargets.org/events"),
    ),
    _source(
        "sse",
        "Sustainable Stock Exchanges Initiative (SSE)",
        _page("https://sseinitiative.org/sse-events"),
        comment="Use the corrected SSE events link.",
    ),
    _source(
        "tnfd",
        "Taskforce on Nature-related Financial Disclosures (TNFD)",
        _page("https://tnfd.global/events/"),
    ),
    _source(
        "usgbc",
        "U.S. Green Building Council (USGBC)",
        _page("https://www.usgbc.org/events"),
    ),
    _source(
        "uae_government_portal",
        "UAE Government Portal",
        _page("https://u.ae/en/media/events", include=UAE_ESG_KEYWORDS),
        comment="Keep only events matching the workbook ESG keyword list.",
    ),
    _source(
        "undp_adaptation",
        "UNDP Climate Change Adaptation",
        _page("https://www.adaptation-undp.org/events"),
        comment="Keep the source even while its event page is empty.",
    ),
    _source(
        "unece",
        "UNECE",
        _page(
            "https://unece.org/info/events/unece-meetings-and-events",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter the broad meetings page for ESG topics.",
    ),
    _source(
        "unepfi",
        "UNEP Finance Initiative",
        _page("https://www.unepfi.org/category/events/"),
    ),
    _source(
        "unfccc",
        "UNFCCC",
        _page("https://unfccc.int/calendar/events-list"),
    ),
    _source(
        "world_bank",
        "World Bank Group",
        _page("https://www.worldbank.org/en/events/events-landing/all"),
        allowed_domains=("worldbank.org",),
    ),
    _source(
        "wef",
        "World Economic Forum",
        _page("https://www.weforum.org/meetings/"),
    ),
    _source(
        "wri",
        "World Resources Institute (WRI)",
        _page("https://www.wri.org/events"),
    ),
    _source(
        "acca",
        "ACCA",
        _page(
            "https://www.accaglobal.com/learning-and-events.html",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter the broad learning and events page for ESG topics.",
    ),
    _source(
        "adnoc",
        "ADNOC",
        _page(
            "https://www.adnoc.ae/en/news-and-media/events",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter the broad events page for ESG topics.",
    ),
    _source(
        "agsi",
        "Arab Gulf States Institute (AGSI)",
        _page("https://agsi.org/events/"),
    ),
    _source(
        "carbon_capture_mena",
        "Carbon Capture MENA Summit",
        _page("https://www.carboncapturemena.com/"),
    ),
    _source(
        "care_expo",
        "CARE - Climate Action & Renewables Expo",
        _page("https://careforsustainability.com/"),
    ),
    _source(
        "climate_action_tracker",
        "Climate Action Tracker",
        _page("https://climateactiontracker.org/blog/"),
    ),
    _source(
        "ecovadis",
        "EcoVadis",
        _page("https://resources.ecovadis.com/webinars"),
    ),
    _source(
        "global_battery_alliance",
        "Global Battery Alliance",
        _page("https://www.globalbattery.org/events/"),
    ),
    _source(
        "green_finance_platform",
        "Green Finance Platform / Green Policy Platform",
        _page("https://www.greenfinanceplatform.org/events"),
    ),
    _source(
        "jll",
        "JLL",
        _page(
            "https://www.jll.com/en-us/webinars",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter the broad webinar page for ESG topics.",
    ),
    _source(
        "kpmg",
        "KPMG",
        _page(
            "https://kpmg.com/us/en/events/upcoming-sustainability-events.html"
        ),
    ),
    _source(
        "mckinsey",
        "McKinsey & Company",
        _page(
            "https://www.mckinsey.com/featured-insights/mckinsey-live",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter McKinsey Live for ESG topics.",
    ),
    _source(
        "meira",
        "Middle East Investor Relations Association (MEIRA)",
        _page("https://conf.meira.me/"),
    ),
    _source(
        "sustainalytics",
        "Morningstar Sustainalytics",
        _page(
            "https://www.sustainalytics.com/esg-research/"
            "-in-category/categories/type/event"
        ),
    ),
    _source(
        "msci",
        "MSCI",
        _page("https://www.msci.com/discover-msci/events"),
    ),
    _source(
        "net_zero_tracker",
        "Net Zero Tracker",
        _page("https://zerotracker.net/insights"),
    ),
    _source(
        "ogmp",
        "Oil & Gas Methane Partnership (OGMP 2.0 / UNEP)",
        _page("https://www.unep.org/events"),
    ),
    _source(
        "ogci",
        "Oil and Gas Climate Initiative (OGCI)",
        _page("https://ccushub.ogci.com/in-conversation/"),
    ),
    _source(
        "pwc",
        "PwC",
        _page(
            "https://viewpoint.pwc.com/us/en/esg/"
            "sustainabilitywebcasts.html"
        ),
    ),
    _source("sgs", "SGS", _page("https://www.sgs.com/en/events")),
    _source(
        "swiss_re",
        "Swiss Re Institute",
        _page(
            "https://www.swissre.com/institute/conferences.html",
            include=GENERAL_ESG_KEYWORDS,
        ),
        comment="Filter the broad conference page for ESG topics.",
    ),
    _source(
        "switch_asia",
        "SWITCH-Asia",
        _page("https://www.switch-asia.eu/event/"),
    ),
    _source(
        "unctad",
        "UNCTAD",
        _page("https://unctad.org/meetings"),
    ),
    _source(
        "undp_climate_aggregation",
        "UNDP Climate Aggregation Platform",
        _page("https://www.undp.org/climate-aggregation-platform/events"),
    ),
    _source(
        "wecoop",
        "WECOOP",
        _page("https://wecoop.eu/events/"),
    ),
)

SOURCE_CONFIGS: Dict[str, RevisedSourceConfig] = {
    config.key: config for config in REVISED_EVENT_SOURCES
}

_ADDITIONAL_LISTING_OVERRIDES = {
    "cbuae": cbuae_events,
    "climate_bonds_initiative": climate_bonds_events,
    "oecd": oecd_events,
    "wef": wef_events,
}


def list_revised_sources() -> List[str]:
    return list(SOURCE_CONFIGS)


def _event_page_config(
    source: RevisedSourceConfig,
    page: RevisedPage,
    include_all_listing_urls: bool = False,
) -> EventPageConfig:
    listing_urls = ()
    if include_all_listing_urls:
        listing_urls = tuple(
            candidate.url for candidate in source.pages if candidate.url != page.url
        )
    allowed_domains = source.allowed_domains
    if include_all_listing_urls and not allowed_domains:
        allowed_domains = tuple(
            dict.fromkeys(_domain(candidate.url) for candidate in source.pages)
        )
    return EventPageConfig(
        key=source.key,
        source=source.source,
        url=page.url,
        category=source.category,
        allowed_domains=allowed_domains,
        allow_url_patterns=page.allow_url_patterns,
        deny_url_patterns=(
            *DEFAULT_DENY_URL_PATTERNS,
            *page.deny_url_patterns,
        ),
        listing_urls=listing_urls,
    )


def _scrape_revised_source_listing(
    source_key: str,
    session: requests.Session,
) -> List[Dict[str, Any]]:
    source = SOURCE_CONFIGS[source_key]
    events: List[Dict[str, Any]] = []

    for index, page in enumerate(source.pages):
        if index == 0 and source_key in _ADDITIONAL_LISTING_OVERRIDES:
            page_events = _ADDITIONAL_LISTING_OVERRIDES[source_key](
                session=session,
                include_details=False,
                include_translation=False,
            )
        else:
            page_events = scrape_event_page(
                _event_page_config(source, page),
                session=session,
            )

        normalized = []
        for event in page_events:
            item = dict(event)
            item["Source"] = source.source
            normalized.append(item)
        normalized = enrich_events(normalized, source.key, source.source)
        filtered = _filter_events(normalized, page)
        removed = len(normalized) - len(filtered)
        if removed:
            print(
                f"Filtered events ({source.key}, page {index + 1}): "
                f"{removed} removed"
            )
        events.extend(filtered)

    return _dedupe_merged_events(events)


def scrape_revised_source(
    source_key: str,
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    limit: int = 0,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    """Scrape one revised source, following detail pages and translating by default."""
    if source_key not in SOURCE_CONFIGS:
        raise KeyError(f"Unknown revised event source: {source_key}")
    if session is None:
        with requests.Session() as client:
            return scrape_revised_source(
                source_key,
                session=client,
                include_details=include_details,
                include_translation=include_translation,
                limit=limit,
                continue_on_error=continue_on_error,
            )

    source = SOURCE_CONFIGS[source_key]
    events = _scrape_revised_source_listing(source_key, session)
    selected = events[:limit] if limit else events

    if include_details:
        selected = enrich_event_details(
            selected,
            _event_page_config(
                source,
                source.pages[0],
                include_all_listing_urls=True,
            ),
            session=session,
            continue_on_error=continue_on_error,
        )
    if include_translation:
        selected = translate_events_to_english(
            selected,
            session=session,
            continue_on_error=continue_on_error,
        )
    return selected


def scrape_all_revised_events(
    source_keys: Optional[Iterable[str]] = None,
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
    on_source_events: Optional[
        Callable[[str, List[Dict[str, Any]]], None]
    ] = None,
) -> List[Dict[str, Any]]:
    """Scrape revised workbook sources without database writes."""
    if session is None:
        with requests.Session() as client:
            return scrape_all_revised_events(
                source_keys=source_keys,
                session=client,
                include_details=include_details,
                include_translation=include_translation,
                continue_on_error=continue_on_error,
                on_source_events=on_source_events,
            )

    keys = list(source_keys or SOURCE_CONFIGS)
    events: List[Dict[str, Any]] = []
    for source_key in keys:
        if source_key not in SOURCE_CONFIGS:
            raise KeyError(f"Unknown revised event source: {source_key}")
        try:
            source_events = scrape_revised_source(
                source_key,
                session=session,
                include_details=include_details,
                include_translation=include_translation,
                continue_on_error=continue_on_error,
            )
            print(f"Total revised events ({source_key}): {len(source_events)}")
            merged_events = _dedupe_merged_events([*events, *source_events])
            new_events = merged_events[len(events) :]
            if on_source_events and new_events:
                on_source_events(source_key, new_events)
            events = merged_events
        except Exception as exc:
            if not continue_on_error:
                raise
            print(f"Failed to process revised source {source_key}: {exc}")
    return events


def _filter_events(
    events: Sequence[Dict[str, Any]],
    page: RevisedPage,
) -> List[Dict[str, Any]]:
    filtered = []
    for event in events:
        text = _event_filter_text(event)
        if page.exclude_keywords and any(
            _contains_keyword(text, keyword) for keyword in page.exclude_keywords
        ):
            continue
        if page.include_keywords and not any(
            _contains_keyword(text, keyword) for keyword in page.include_keywords
        ):
            continue
        filtered.append(event)
    return filtered


def _event_filter_text(event: Dict[str, Any]) -> str:
    values = [
        event.get("Event Name"),
        event.get("Summary"),
        event.get("Tags"),
        event.get("Topics"),
    ]
    parts = []
    for value in values:
        if isinstance(value, (list, tuple, set)):
            parts.extend(str(item) for item in value if item)
        elif value:
            parts.append(str(value))
    return " ".join(parts)


def _contains_keyword(text: str, keyword: str) -> bool:
    normalized_text = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    normalized_keyword = re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()
    if not normalized_keyword:
        return False
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(normalized_keyword)}(?![a-z0-9])",
            normalized_text,
        )
    )


def _dedupe_merged_events(
    events: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for event in events:
        title = re.sub(
            r"[^a-z0-9]+",
            " ",
            str(event.get("Event Name") or "").lower(),
        ).strip()
        start_date = str(event.get("Start Date") or "")
        url = _normalized_url(event.get("Event URL"))
        identity = (title, start_date) if title and start_date else (url, title)
        if identity in seen:
            continue
        seen.add(identity)
        deduped.append(event)
    return deduped


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _normalized_url(value: Any) -> str:
    if not value:
        return ""
    parsed = urlparse(str(value))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            path,
            "",
            parsed.query,
            "",
        )
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _csv_value(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, default=_json_default)
    return "" if value is None else value


def write_events_file(events: List[Dict[str, Any]], output_path: str) -> Path:
    path = Path(output_path)
    suffix = path.suffix.lower()
    if suffix not in {".json", ".csv"}:
        raise ValueError("Output path must end in .json or .csv")

    path.parent.mkdir(parents=True, exist_ok=True)
    if suffix == ".json":
        path.write_text(
            json.dumps(events, ensure_ascii=False, indent=2, default=_json_default),
            encoding="utf-8",
        )
        return path

    fieldnames = []
    for event in events:
        for key in event:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow({key: _csv_value(event.get(key)) for key in fieldnames})
    return path


def _make_source_scraper(source_key: str):
    def _scraper() -> List[Dict[str, Any]]:
        return scrape_revised_source(source_key)

    _scraper.__name__ = f"{source_key}_events"
    _scraper.__doc__ = f"Scrape events for {SOURCE_CONFIGS[source_key].source}."
    return _scraper


for _source_key in SOURCE_CONFIGS:
    globals()[f"{_source_key}_events"] = _make_source_scraper(_source_key)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description=(
            "Test revised event source scrapers without saving to the database."
        ),
        epilog=(
            "Examples:\n"
            "  python -m events.revised_sources --list-sources\n"
            "  python -m events.revised_sources --source adb --json --limit 5\n"
            "  python -m events.revised_sources --source aiib "
            "--output extracted_events/aiib.json\n"
            "  python -m events.revised_sources --source adb --no-details "
            "--no-translate"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--source",
        action="append",
        choices=list_revised_sources(),
        help="Source key to scrape. Can be passed multiple times.",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="Print source keys, links, and filter comments, then exit.",
    )
    parser.add_argument("--json", action="store_true", help="Print events as JSON.")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit output; for one source this also limits detail-page requests.",
    )
    details = parser.add_mutually_exclusive_group()
    details.add_argument(
        "--details",
        dest="include_details",
        action="store_true",
        default=True,
        help="Extract every event detail page (default).",
    )
    details.add_argument(
        "--no-details",
        dest="include_details",
        action="store_false",
        help="Scrape listing pages only.",
    )
    translation = parser.add_mutually_exclusive_group()
    translation.add_argument(
        "--translate",
        dest="include_translation",
        action="store_true",
        default=True,
        help="Translate non-English events into English (default).",
    )
    translation.add_argument(
        "--no-translate",
        dest="include_translation",
        action="store_false",
        help="Keep original-language text.",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="Print only scraper counts.",
    )
    parser.add_argument(
        "--output",
        help="Write extracted events to a .json or .csv file.",
    )
    args = parser.parse_args()

    if args.list_sources:
        for key, source in SOURCE_CONFIGS.items():
            urls = ", ".join(page.url for page in source.pages)
            print(f"{key}: {source.source} | {urls}")
            if source.comment:
                print(f"  Filter/comment: {source.comment}")
        return

    if args.source and len(args.source) == 1:
        events = scrape_revised_source(
            args.source[0],
            include_details=args.include_details,
            include_translation=args.include_translation,
            limit=args.limit,
        )
        print(f"Total revised events ({args.source[0]}): {len(events)}")
    else:
        events = scrape_all_revised_events(
            source_keys=args.source,
            include_details=args.include_details,
            include_translation=args.include_translation,
        )

    if args.summary_only:
        print(f"Total revised events scraped: {len(events)}")
        return

    displayed = events[: args.limit] if args.limit else events
    if args.output:
        output = write_events_file(displayed, args.output)
        print(f"Saved {len(displayed)} events to {output.resolve()}")

    if args.json:
        print(
            json.dumps(
                displayed,
                ensure_ascii=False,
                indent=2,
                default=_json_default,
            )
        )
        return

    for event in displayed:
        print(
            f"{event.get('Source')} | {event.get('Start Date') or 'date n/a'} | "
            f"{event.get('Event Name')} | {event.get('Event URL')}"
        )


__all__ = [
    "REVISED_EVENT_SOURCES",
    "SOURCE_CONFIGS",
    "RevisedPage",
    "RevisedSourceConfig",
    "list_revised_sources",
    "scrape_all_revised_events",
    "scrape_revised_source",
    "write_events_file",
] + [f"{key}_events" for key in SOURCE_CONFIGS]


if __name__ == "__main__":
    main()
