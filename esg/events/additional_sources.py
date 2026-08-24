import hashlib
import html
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from events.event_page_scraper import (
    EventPageConfig,
    enrich_event_details,
    scrape_event_page,
)
from events.event_translator import translate_events_to_english


DEFAULT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
}

GRI_EVENTS_URL = "https://www.globalreporting.org/news/events/"
GGGI_EVENTS_URL = "https://gggi.org/events/list/"
SUSTAINABLE_FITCH_EVENTS_URL = "https://www.sustainablefitch.com/events"
SUSTAINABLE_FITCH_DATA_URL = (
    "https://www.sustainablefitch.com/page-data/events/page-data.json"
)
FITCH_RATINGS_EVENTS_URL = "https://www.fitchratings.com/events"
FITCH_RATINGS_DATA_URL = (
    "https://www.fitchratings.com/page-data/events/page-data.json"
)
SP_GLOBAL_EVENTS_URL = (
    "https://www.spglobal.com/en/research-insights/events/featured"
)
SP_GLOBAL_TOKEN_URL = (
    "https://www.spglobal.com/content/spglobal/api/servlets/"
    "searchAuthToken.generate.json"
)
SP_GLOBAL_QUERY_URL = (
    "https://www.spglobal.com/api/apps/spglobal-prod/query/spglobal-prod"
)
CBUAE_EVENTS_URL = "https://www.centralbank.ae/en/news-and-publications/events/"
CBUAE_EVENTS_RSS_URL = "https://www.centralbank.ae/en/rss-feed/events-rss-feed/"
CLIMATE_BONDS_EVENTS_URL = (
    "https://www.climatebonds.net/news-events/events-training-webinars"
)
OECD_EVENTS_URL = "https://www.oecd.org/en/events.html"
WEF_MEETINGS_URL = "https://www.weforum.org/meetings/"

SP_GLOBAL_XHR_HEADERS = {
    **DEFAULT_HEADERS,
    "accept": "application/json, text/plain, */*",
    "origin": "https://www.spglobal.com",
    "referer": SP_GLOBAL_EVENTS_URL,
    "sec-ch-ua": (
        '"Not A(Brand";v="8", "Chromium";v="132", '
        '"Google Chrome";v="132"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-requested-with": "XMLHttpRequest",
}

DATE_LINE_PATTERN = (
    r"(?:\d{1,2}\s*[-\u2013\u2014]\s*\d{1,2}\s+[A-Za-z]+\s+20\d{2}|"
    r"\d{1,2}\s+[A-Za-z]+\s+20\d{2})"
)


def _gri_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape GRI's highlighted upcoming event cards."""
    client = session or requests.Session()
    response = client.get(GRI_EVENTS_URL, headers=DEFAULT_HEADERS, timeout=30)
    print(f"Response status code (Global Reporting Initiative): {response.status_code}")
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    events = []
    for card in soup.select("a.card.card--event[href]"):
        title_node = card.select_one(".card__header")
        date_node = card.select_one(".card__body .p-small")
        title = _clean_text(title_node.get_text(" ", strip=True) if title_node else None)
        if not title or not date_node:
            continue

        start_date, end_date = _parse_date_range(
            date_node.get_text(" ", strip=True)
        )
        if not start_date:
            continue

        event_url = urljoin(GRI_EVENTS_URL, card.get("href"))
        summary_node = card.select_one(".card__text")
        info_node = card.select_one(".card__info")
        image_url = _background_image_url(card, GRI_EVENTS_URL)
        tags = _unique_strings(
            [
                "Sustainability reporting",
                _clean_text(info_node.get_text(" ", strip=True))
                if info_node
                else None,
            ]
        )
        events.append(
            _event(
                source_key="gri",
                source="Global Reporting Initiative (GRI)",
                title=title,
                event_url=event_url,
                start_date=start_date,
                end_date=end_date,
                image_url=image_url,
                summary=_clean_text(
                    summary_node.get_text(" ", strip=True)
                    if summary_node
                    else None
                ),
                tags=tags,
            )
        )
    return _dedupe(events)


def _gggi_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Keep only real GGGI event cards and deduplicate them by detail URL."""
    candidates = scrape_event_page(
        ADDITIONAL_SOURCE_CONFIGS["gggi"],
        session=session,
    )
    events_by_url: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        event_url = _clean_text(candidate.get("Event URL"))
        if not _is_gggi_event_detail_url(event_url):
            continue

        event = dict(candidate)
        event["Event Name"] = _decode_gggi_text(event.get("Event Name"))
        event["Summary"] = _decode_gggi_text(event.get("Summary"))
        event["Source"] = "Global Green Growth Institute (GGGI)"
        existing = events_by_url.get(event_url)
        if existing is None or _gggi_event_quality(event) > _gggi_event_quality(
            existing
        ):
            events_by_url[event_url] = event
    return list(events_by_url.values())


def _is_gggi_event_detail_url(value: Optional[str]) -> bool:
    if not value:
        return False
    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path.rstrip("/") + "/"
    return (
        host in {"gggi.org", "www.gggi.org"} and path.startswith("/event/")
    ) or host == "globalgreengrowthweek.gggi.org"


def _decode_gggi_text(value: Any) -> Optional[str]:
    text = _clean_text(value)
    if not text:
        return None
    text = text.replace("\\n", " ")
    for _ in range(2):
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return _clean_text(BeautifulSoup(text, "html.parser").get_text(" ", strip=True))


def _gggi_event_quality(event: Dict[str, Any]) -> int:
    title = str(event.get("Event Name") or "")
    return sum(
        (
            4 if event.get("Start Date") else 0,
            3 if event.get("End Date") else 0,
            2 if event.get("Summary") else 0,
            1 if event.get("Venue Name") or event.get("Venue Address") else 0,
            -5 if "\ufffd" in title else 0,
        )
    )


def normalize_gggi_events(
    events: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Decode GGGI display fields while retaining raw structured data."""
    normalized_events = []
    for event in events:
        normalized = dict(event)
        for field in ("Event Name", "Summary", "Detail Page Title", "Detail Text"):
            if normalized.get(field):
                normalized[field] = _decode_gggi_text(normalized[field])
        normalized_events.append(normalized)
    return normalized_events


def _sustainable_fitch_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape Sustainable Fitch's published Gatsby event data."""
    return _fitch_contentful_listing_events(
        source_key="sustainable_fitch",
        source="Sustainable Fitch",
        events_url=SUSTAINABLE_FITCH_EVENTS_URL,
        data_url=SUSTAINABLE_FITCH_DATA_URL,
        session=session,
    )


def _fitch_ratings_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape Fitch Ratings' published Gatsby event data."""
    return _fitch_contentful_listing_events(
        source_key="fitch_ratings",
        source="Fitch Ratings",
        events_url=FITCH_RATINGS_EVENTS_URL,
        data_url=FITCH_RATINGS_DATA_URL,
        session=session,
    )


def _fitch_contentful_listing_events(
    source_key: str,
    source: str,
    events_url: str,
    data_url: str,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Normalize upcoming events from a Fitch Contentful/Gatsby feed."""
    client = session or requests.Session()
    response = client.get(
        data_url,
        headers=DEFAULT_HEADERS,
        timeout=30,
    )
    print(f"Response status code ({source}): {response.status_code}")
    response.raise_for_status()

    nodes = (
        response.json()
        .get("result", {})
        .get("data", {})
        .get("allContentfulEvent", {})
        .get("nodes", [])
    )
    if not isinstance(nodes, list):
        raise ValueError(f"{source} event feed did not contain an event list")

    today = datetime.now(timezone.utc).date()
    events = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        title = _clean_text(node.get("title"))
        start = _parse_datetime(node.get("isoStartTime") or node.get("startDate"))
        end = _parse_datetime(node.get("isoEndTime") or node.get("endDate"))
        last_event_date = (end or start).date() if (end or start) else None
        if not title or not start or not last_event_date or last_event_date < today:
            continue

        event_url = _absolute_url(node.get("vanityUrl"), events_url)
        location_parts = _location_parts(
            [
                node.get("locationAddress"),
                node.get("locationCity"),
                node.get("locationCountry"),
            ]
        )
        image_url = _fitch_image_url(node.get("image"))
        countries = _titles(node.get("countries"))
        regions = _titles(node.get("regions"))
        sectors = _titles(node.get("sectors"))
        topics = _slugs(node.get("topics"))
        languages = _slugs(node.get("languages"))
        tags = _unique_strings(
            [node.get("eventType")]
            + sectors
            + regions
            + countries
            + topics
        )
        event = _event(
            source_key=source_key,
            source=source,
            title=title,
            event_url=event_url,
            event_id=node.get("eventId"),
            start_date=start.date(),
            end_date=end.date() if end else None,
            start_time=_time_or_none(start),
            end_time=_time_or_none(end),
            timezone_name=node.get("timeZone"),
            image_url=image_url,
            venue_name=_clean_text(node.get("locationName")),
            venue_address=", ".join(location_parts) or None,
            organizer_name=source,
            tags=tags,
        )
        event.update(
            {
                "Fitch Event ID": node.get("eventId"),
                "Event Type": _clean_text(node.get("eventType")),
                "Countries": countries,
                "Regions": regions,
                "Sectors": sectors,
                "Topics": topics,
                "Languages": languages,
                "Listing ISO Start": node.get("isoStartTime")
                or node.get("startDate"),
                "Listing ISO End": node.get("isoEndTime") or node.get("endDate"),
                "Time Zone Abbreviation": _clean_text(
                    node.get("timeZoneAbbreviation")
                ),
                "Relative Event URL": _clean_text(node.get("relativeVanityUrl")),
                "Image Title": _clean_text(
                    node.get("image", {}).get("title")
                    if isinstance(node.get("image"), dict)
                    else None
                ),
            }
        )
        events.append(event)
    return _dedupe(events)


def _fitch_image_url(image: Any) -> Optional[str]:
    if not isinstance(image, dict):
        return None
    fixed = image.get("fixed")
    if isinstance(fixed, dict) and fixed.get("src"):
        return _clean_text(fixed.get("src"))
    gatsby_data = image.get("gatsbyImageData")
    if not isinstance(gatsby_data, dict):
        return None
    images = gatsby_data.get("images")
    if not isinstance(images, dict):
        return None
    fallback = images.get("fallback")
    if isinstance(fallback, dict):
        return _clean_text(fallback.get("src"))
    return None


def _sp_global_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape all upcoming S&P Global events through its public search API."""
    client = session or requests.Session()
    token_response = client.get(
        SP_GLOBAL_TOKEN_URL,
        params={"division": "corporate"},
        headers=SP_GLOBAL_XHR_HEADERS,
        timeout=30,
    )
    print(f"Response status code (S&P Global token): {token_response.status_code}")
    token_response.raise_for_status()
    token = token_response.json().get("token")
    if not token:
        raise ValueError("S&P Global token response did not contain a token")

    query_headers = {
        **SP_GLOBAL_XHR_HEADERS,
        "authorization": f"Bearer {token}",
    }
    now_utc = (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    rows_per_page = 100
    page_number = 1
    documents = []

    while True:
        params = [
            ("q", "*:*"),
            ("rows", str(rows_per_page)),
            ("pagenum", str(page_number)),
            ("sort", "es_event_start_date_dt asc"),
            ("fq", f"es_event_end_date_dt:[{now_utc} TO *]"),
            ("fq", 'es_content_type_s:("Events")'),
            (
                "cfl",
                "es_event_start_date_dt,es_event_end_date_dt,"
                "es_primary_image_url_s,es_event_category_s,"
                "es_event_subcontenttype_ss,es_location_ss",
            ),
            ("division", "corporate"),
        ]
        response = client.get(
            SP_GLOBAL_QUERY_URL,
            params=params,
            headers=query_headers,
            timeout=30,
        )
        print(
            f"Response status code (S&P Global page {page_number}): "
            f"{response.status_code}"
        )
        response.raise_for_status()
        response_data = response.json().get("response", {})
        page_documents = response_data.get("docs", [])
        documents.extend(page_documents)
        total = int(response_data.get("numFound") or len(documents))
        if not page_documents or len(documents) >= total:
            break
        page_number += 1

    events = []
    for document in documents:
        title = _clean_text(document.get("es_title_t"))
        start = _parse_datetime(document.get("es_event_start_date_dt"))
        end = _parse_datetime(document.get("es_event_end_date_dt"))
        if not title or not start:
            continue

        event_url = _absolute_url(document.get("es_url_s"), SP_GLOBAL_EVENTS_URL)
        location = _first_text(document.get("es_location_ss"))
        division = _clean_text(document.get("es_division_s"))
        category = _first_text(document.get("es_event_category_s"))
        subtypes = _as_strings(document.get("es_event_subcontenttype_ss"))
        summary = _first_text(document.get("es_description_t")) or _first_text(
            document.get("es_body_content_txt")
        )
        tags = _unique_strings([category, division] + subtypes)
        events.append(
            _event(
                source_key="sp_global",
                source="S&P Global",
                title=title,
                event_url=event_url,
                start_date=start.date(),
                end_date=end.date() if end else None,
                start_time=_time_or_none(start),
                end_time=_time_or_none(end),
                timezone_name=start.tzname(),
                image_url=_first_text(document.get("es_primary_image_url_s")),
                venue_name=location,
                venue_address=location,
                organizer_name=division,
                summary=summary[:2000] if summary else None,
                tags=tags,
            )
        )
    return _dedupe(events)


def _cbuae_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape CBUAE's event feed and enrich each item from its official detail page."""
    client = session or requests.Session()
    feed = _reader_markdown(CBUAE_EVENTS_RSS_URL, client, "CBUAE events feed")
    item_pattern = re.compile(
        r"(?m)^###\s+\[([^\]]+)\]\((https://www\.centralbank\.ae/[^)]+)\)\s*$"
    )
    events = []
    for match in item_pattern.finditer(feed):
        title = _clean_text(match.group(1))
        event_url = match.group(2)
        if not title:
            continue

        detail = _reader_markdown(event_url, client, f"CBUAE event: {title}")
        heading = re.search(
            rf"(?mi)^#{{1,3}}\s+{re.escape(title)}\s*$",
            detail,
        )
        event_text = detail[heading.end() :] if heading else detail
        start_date, end_date = _first_date_range(event_text)
        if not start_date:
            continue
        start_time, end_time, timezone_name = _first_time_range(event_text)
        events.append(
            _event(
                source_key="cbuae",
                source="Central Bank of the UAE (CBUAE)",
                title=title,
                event_url=event_url,
                start_date=start_date,
                end_date=end_date,
                start_time=start_time,
                end_time=end_time,
                timezone_name=timezone_name,
                organizer_name="Central Bank of the UAE",
                tags=["Events and Workshops"],
            )
        )
    return _dedupe(events)


def _climate_bonds_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape Climate Bonds upcoming events through a public reader fallback."""
    client = session or requests.Session()
    markdown = _reader_markdown(
        CLIMATE_BONDS_EVENTS_URL,
        client,
        "Climate Bonds Initiative",
    )
    section = _between(markdown, "Upcoming events", "SUBSCRIBE")
    pattern = re.compile(
        rf"(?m)^(?<!!)\[([^\]]+)\]\((https?://www\.climatebonds\.net/[^)]+)\)"
        rf"\s*\n+\s*({DATE_LINE_PATTERN})\s*$"
    )
    events = []
    for match in pattern.finditer(section):
        start_date, end_date = _parse_date_range(match.group(3))
        if not start_date:
            continue
        events.append(
            _event(
                source_key="climate_bonds_initiative",
                source="Climate Bonds Initiative",
                title=_clean_text(match.group(1)) or "Climate Bonds event",
                event_url=match.group(2).replace("http://", "https://", 1),
                start_date=start_date,
                end_date=end_date,
                organizer_name="Climate Bonds Initiative",
                tags=["Sustainable finance", "Climate bonds"],
            )
        )
    return _dedupe(events)


def _oecd_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape OECD featured and upcoming events through a public reader fallback."""
    client = session or requests.Session()
    markdown = _reader_markdown(OECD_EVENTS_URL, client, "OECD")
    events = []

    featured = _nonempty_lines(_between(markdown, "Featured", "Upcoming events"))
    for index, line in enumerate(featured):
        if not _is_date_line(line) or index < 2:
            continue
        start_date, end_date = _parse_date_range(line)
        title = _clean_text(featured[index - 2])
        topic = _clean_text(featured[index - 1])
        if title and start_date:
            events.append(
                _event(
                    source_key="oecd",
                    source="OECD",
                    title=title,
                    event_url=OECD_EVENTS_URL,
                    start_date=start_date,
                    end_date=end_date,
                    organizer_name="OECD",
                    tags=_unique_strings(["Featured", topic]),
                )
            )

    metadata = {
        "forum",
        "in person",
        "virtual",
        "webinar",
        "conference",
        "hybrid",
        "workshop",
        "see all",
    }
    upcoming = _nonempty_lines(
        _between(markdown, "Upcoming events", "Recent replays")
    )
    previous_date_index = -1
    for index, line in enumerate(upcoming):
        if not _is_date_line(line):
            continue
        title = None
        title_index = None
        for candidate_index in range(index - 1, max(previous_date_index, index - 6), -1):
            candidate = _clean_text(upcoming[candidate_index])
            if not candidate:
                continue
            lowered = candidate.lower()
            if lowered in metadata or re.search(r"\.[a-z]{2,}$", lowered):
                continue
            title = candidate
            title_index = candidate_index
            break
        start_date, end_date = _parse_date_range(line)
        if title and start_date:
            tags = [
                candidate
                for candidate in upcoming[previous_date_index + 1 : title_index]
                if candidate.lower() in metadata - {"see all"}
            ]
            events.append(
                _event(
                    source_key="oecd",
                    source="OECD",
                    title=title,
                    event_url=OECD_EVENTS_URL,
                    start_date=start_date,
                    end_date=end_date,
                    organizer_name="OECD",
                    tags=_unique_strings(tags),
                )
            )
        previous_date_index = index
    return _dedupe(events)


def _wef_listing_events(
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape WEF upcoming meetings through a public reader fallback."""
    client = session or requests.Session()
    markdown = _reader_markdown(WEF_MEETINGS_URL, client, "World Economic Forum")
    section = _between(markdown, "### Upcoming meetings", "### Past meetings")
    pattern = re.compile(
        rf"(?m)^(?<!!)\[([^\]]+)\]\((https://www\.weforum\.org/meetings/[^)]+)\)"
        rf"\s*\n+\s*({DATE_LINE_PATTERN})\s*\n+\s*([^\n]+)"
    )
    events = []
    for match in pattern.finditer(section):
        start_date, end_date = _parse_date_range(match.group(3))
        if not start_date:
            continue
        prefix = section[max(0, match.start() - 500) : match.start()]
        images = re.findall(r"!\[[^\]]*\]\((https://assets\.weforum\.org/[^)]+)\)", prefix)
        location = _clean_text(match.group(4))
        events.append(
            _event(
                source_key="wef",
                source="World Economic Forum",
                title=_clean_text(match.group(1)) or "World Economic Forum meeting",
                event_url=match.group(2),
                start_date=start_date,
                end_date=end_date,
                image_url=images[-1] if images else None,
                venue_name=location,
                venue_address=location,
                organizer_name="World Economic Forum",
                tags=["Global forum", "Meeting"],
            )
        )
    return _dedupe(events)


def _event(
    source_key: str,
    source: str,
    title: str,
    event_url: str,
    event_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    start_time: Optional[Any] = None,
    end_time: Optional[Any] = None,
    timezone_name: Optional[str] = None,
    image_url: Optional[str] = None,
    venue_name: Optional[str] = None,
    venue_address: Optional[str] = None,
    organizer_name: Optional[str] = None,
    summary: Optional[str] = None,
    tags: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    return {
        "Event Name": title,
        "Event ID": event_id or _stable_id(source_key, event_url, title, start_date),
        "Event URL": event_url,
        "Start Date": start_date,
        "End Date": end_date,
        "Start Time": start_time,
        "End Time": end_time,
        "Timezone": timezone_name,
        "Image URL": image_url,
        "Ticket Price": None,
        "Tickets URL": None,
        "Venue Name": venue_name,
        "Venue Address": venue_address,
        "Organizer Name": organizer_name,
        "Organizer URL": None,
        "Summary": summary,
        "Tags": list(tags or []),
        "Source": source,
        "Month": start_date.strftime("%B %Y") if start_date else None,
    }


def _parse_date_range(value: str) -> Tuple[Optional[date], Optional[date]]:
    text = _clean_text(value)
    if not text:
        return None, None
    match = re.fullmatch(
        r"(\d{1,2})\s*[-\u2013\u2014]\s*(\d{1,2})\s+([A-Za-z]+)\s+(20\d{2})",
        text,
    )
    if match:
        start = date_parser.parse(
            f"{match.group(1)} {match.group(3)} {match.group(4)}"
        ).date()
        end = date_parser.parse(
            f"{match.group(2)} {match.group(3)} {match.group(4)}"
        ).date()
        return start, end
    full_range = re.fullmatch(
        r"(\d{1,2}\s+[A-Za-z]+\s+20\d{2})\s*[-\u2013\u2014]\s*"
        r"(\d{1,2}\s+[A-Za-z]+\s+20\d{2})",
        text,
    )
    if full_range:
        return (
            date_parser.parse(full_range.group(1)).date(),
            date_parser.parse(full_range.group(2)).date(),
        )
    try:
        return date_parser.parse(text, fuzzy=True).date(), None
    except (TypeError, ValueError, OverflowError):
        return None, None


def _parse_datetime(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return date_parser.isoparse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _first_date_range(value: str) -> Tuple[Optional[date], Optional[date]]:
    full_range = re.search(
        r"\b\d{1,2}\s+[A-Za-z]+\s+20\d{2}\s*[-\u2013\u2014]\s*"
        r"\d{1,2}\s+[A-Za-z]+\s+20\d{2}\b",
        value,
    )
    if full_range:
        return _parse_date_range(full_range.group(0))
    single = re.search(r"\b\d{1,2}\s+[A-Za-z]+\s+20\d{2}\b", value)
    return _parse_date_range(single.group(0)) if single else (None, None)


def _first_time_range(value: str):
    match = re.search(
        r"\b(\d{1,2}:\d{2})\s*[-\u2013\u2014]\s*(\d{1,2}:\d{2})"
        r"(?:\s+([A-Z]{2,6}))?\b",
        value,
    )
    if not match:
        return None, None, None
    try:
        start_time = date_parser.parse(match.group(1)).time()
        end_time = date_parser.parse(match.group(2)).time()
    except (TypeError, ValueError, OverflowError):
        return None, None, match.group(3)
    if start_time.isoformat() == "00:00:00" and end_time.isoformat() == "00:00:00":
        return None, None, match.group(3)
    return start_time, end_time, match.group(3)


def _time_or_none(value: Optional[datetime]):
    if not value:
        return None
    parsed_time = value.time()
    return parsed_time if parsed_time.isoformat() != "00:00:00" else None


def _reader_markdown(
    source_url: str,
    session: requests.Session,
    source_name: str,
) -> str:
    reader_url = f"https://r.jina.ai/{source_url}"
    response = session.get(
        reader_url,
        headers={"accept": "text/plain", "user-agent": DEFAULT_HEADERS["user-agent"]},
        timeout=60,
    )
    print(f"Response status code ({source_name} reader): {response.status_code}")
    response.raise_for_status()
    text = response.text
    if "Performing security verification" in text or "requiring CAPTCHA" in text:
        raise RuntimeError(f"{source_name} reader returned a security challenge")
    return text


def _between(value: str, start_marker: str, end_marker: str) -> str:
    start = value.lower().find(start_marker.lower())
    if start < 0:
        return ""
    start += len(start_marker)
    end = value.lower().find(end_marker.lower(), start)
    return value[start : end if end >= 0 else None]


def _nonempty_lines(value: str) -> List[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _is_date_line(value: str) -> bool:
    return bool(re.fullmatch(DATE_LINE_PATTERN, value.strip()))


def _background_image_url(node: Any, page_url: str) -> Optional[str]:
    image_node = node.select_one("[style*='background-image']")
    if not image_node:
        return None
    match = re.search(r"url\(['\"]?([^'\")]+)", image_node.get("style", ""))
    return urljoin(page_url, match.group(1)) if match else None


def _absolute_url(value: Any, page_url: str) -> str:
    text = _clean_text(value)
    if not text:
        return page_url
    if re.match(r"^https?://", text, flags=re.I):
        return text
    if text.startswith("//"):
        return "https:" + text
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}/", text):
        return "https://" + text
    return urljoin(page_url, text)


def _titles(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    return [
        text
        for item in values
        if isinstance(item, dict)
        for text in [_clean_text(item.get("title"))]
        if text
    ]


def _slugs(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    results = []
    for item in values:
        if not isinstance(item, dict):
            continue
        slug = _clean_text(item.get("slug"))
        if slug:
            results.append(slug.rsplit("/", 1)[-1].replace("-", " ").title())
    return results


def _as_strings(value: Any) -> List[str]:
    if isinstance(value, list):
        return [text for item in value for text in [_clean_text(item)] if text]
    text = _clean_text(value)
    return [text] if text else []


def _first_text(value: Any) -> Optional[str]:
    values = _as_strings(value)
    return values[0] if values else None


def _unique_strings(values: Iterable[Any]) -> List[str]:
    seen = set()
    results = []
    for value in values:
        text = _clean_text(value)
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        results.append(text)
    return results


def _location_parts(values: Iterable[Any]) -> List[str]:
    results = []
    combined = ""
    for value in values:
        text = _clean_text(value)
        if not text or text.lower() in combined.lower():
            continue
        results.append(text)
        combined = ", ".join(results)
    return results


def _stable_id(
    source_key: str,
    event_url: str,
    title: str,
    start_date: Optional[date],
) -> str:
    raw = "|".join(
        str(value or "").strip().lower()
        for value in (source_key, event_url, title, start_date)
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _dedupe(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    results = []
    for event in events:
        key = (
            event.get("Event URL"),
            str(event.get("Event Name") or "").lower(),
            str(event.get("Start Date") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        results.append(event)
    return results


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


ADDITIONAL_SOURCE_CONFIGS = {
    "gri": EventPageConfig(
        key="gri",
        source="Global Reporting Initiative (GRI)",
        category="ESG disclosure / standards",
        url=GRI_EVENTS_URL,
    ),
    "gggi": EventPageConfig(
        key="gggi",
        source="Global Green Growth Institute (GGGI)",
        category="Intergovernmental organization",
        url=GGGI_EVENTS_URL,
        allowed_domains=("gggi.org",),
        allow_url_patterns=(
            r"^https://(?:www\.)?gggi\.org/event/",
            r"^https://globalgreengrowthweek\.gggi\.org/",
        ),
    ),
    "sustainable_fitch": EventPageConfig(
        key="sustainable_fitch",
        source="Sustainable Fitch",
        category="ESG ratings / research",
        url=SUSTAINABLE_FITCH_EVENTS_URL,
        allowed_domains=("sustainablefitch.com", "fitchratings.com"),
    ),
    "fitch_ratings": EventPageConfig(
        key="fitch_ratings",
        source="Fitch Ratings",
        category="Credit ratings / research",
        url=FITCH_RATINGS_EVENTS_URL,
        allowed_domains=("fitchratings.com",),
    ),
    "sp_global": EventPageConfig(
        key="sp_global",
        source="S&P Global",
        category="ESG ratings / research",
        url=SP_GLOBAL_EVENTS_URL,
    ),
    "cbuae": EventPageConfig(
        key="cbuae",
        source="Central Bank of the UAE (CBUAE)",
        category="Regulator / central bank",
        url=CBUAE_EVENTS_URL,
    ),
    "climate_bonds_initiative": EventPageConfig(
        key="climate_bonds_initiative",
        source="Climate Bonds Initiative",
        category="Sustainable finance standards",
        url=CLIMATE_BONDS_EVENTS_URL,
    ),
    "oecd": EventPageConfig(
        key="oecd",
        source="OECD",
        category="Intergovernmental organization",
        url=OECD_EVENTS_URL,
    ),
    "wef": EventPageConfig(
        key="wef",
        source="World Economic Forum",
        category="Global forum / business",
        url=WEF_MEETINGS_URL,
    ),
}

_ADDITIONAL_LISTING_SCRAPERS = {
    "gri": _gri_listing_events,
    "gggi": _gggi_listing_events,
    "sustainable_fitch": _sustainable_fitch_listing_events,
    "fitch_ratings": _fitch_ratings_listing_events,
    "sp_global": _sp_global_listing_events,
    "cbuae": _cbuae_listing_events,
    "climate_bonds_initiative": _climate_bonds_listing_events,
    "oecd": _oecd_listing_events,
    "wef": _wef_listing_events,
}


def scrape_additional_source(
    source_key: str,
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    """Scrape an additional source with detail and translation enabled by default."""
    if session is None:
        with requests.Session() as client:
            return scrape_additional_source(
                source_key,
                session=client,
                include_details=include_details,
                include_translation=include_translation,
                continue_on_error=continue_on_error,
            )

    events = _ADDITIONAL_LISTING_SCRAPERS[source_key](session=session)
    if include_details:
        events = enrich_event_details(
            events,
            ADDITIONAL_SOURCE_CONFIGS[source_key],
            session=session,
            continue_on_error=continue_on_error,
        )
    if source_key == "gggi":
        events = normalize_gggi_events(events)
    if include_translation:
        events = translate_events_to_english(
            events,
            session=session,
            continue_on_error=continue_on_error,
        )
    return events


def gri_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "gri",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def gggi_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "gggi",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def sustainable_fitch_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "sustainable_fitch",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def fitch_ratings_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "fitch_ratings",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def sp_global_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "sp_global",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def cbuae_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "cbuae",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def climate_bonds_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "climate_bonds_initiative",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def oecd_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "oecd",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


def wef_events(
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    return scrape_additional_source(
        "wef",
        session=session,
        include_details=include_details,
        include_translation=include_translation,
        continue_on_error=continue_on_error,
    )


ADDITIONAL_SOURCE_KEYS = tuple(_ADDITIONAL_LISTING_SCRAPERS)


def all_additional_source_events(
    source_keys: Optional[Iterable[str]] = None,
    session: Optional[requests.Session] = None,
    include_details: bool = True,
    include_translation: bool = True,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    """Scrape the registered additional sources for the event DB pipeline."""
    if session is None:
        with requests.Session() as client:
            return all_additional_source_events(
                source_keys=source_keys,
                session=client,
                include_details=include_details,
                include_translation=include_translation,
                continue_on_error=continue_on_error,
            )

    keys = list(source_keys or ADDITIONAL_SOURCE_KEYS)
    events = []
    for source_key in keys:
        if source_key not in _ADDITIONAL_LISTING_SCRAPERS:
            raise KeyError(f"Unknown additional event source: {source_key}")
        try:
            source_events = scrape_additional_source(
                source_key,
                session=session,
                include_details=include_details,
                include_translation=include_translation,
                continue_on_error=continue_on_error,
            )
            print(f"Total additional events ({source_key}): {len(source_events)}")
            events.extend(source_events)
        except Exception as exc:
            if not continue_on_error:
                raise
            print(f"Failed to scrape additional source {source_key}: {exc}")
    return _dedupe(events)


__all__ = [
    "ADDITIONAL_SOURCE_CONFIGS",
    "ADDITIONAL_SOURCE_KEYS",
    "all_additional_source_events",
    "cbuae_events",
    "climate_bonds_events",
    "fitch_ratings_events",
    "gggi_events",
    "gri_events",
    "oecd_events",
    "normalize_gggi_events",
    "sp_global_events",
    "sustainable_fitch_events",
    "scrape_additional_source",
    "wef_events",
]
