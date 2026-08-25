import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser


DEFAULT_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/132.0.0.0 Safari/537.36"
    ),
}

DEFAULT_DENY_URL_PATTERNS = (
    r"/login",
    r"/sign[-_]?in",
    r"/register$",
    r"/registration$",
    r"/search",
    r"/contact",
    r"/about",
    r"/privacy",
    r"/terms",
    r"\?site=",
    r"\?past=",
    r"/setLocale/",
    r"facebook\.com/sharer",
    r"twitter\.com/share",
    r"linkedin\.com/.*/share",
    r"mailto:",
    r"javascript:",
    r"#",
)

DEFAULT_DENY_TITLE_PATTERNS = (
    r"^events?$",
    r"^calendar$",
    r"^read more$",
    r"^learn more$",
    r"^view all$",
    r"^upcoming$",
    r"^past$",
    r"^home$",
    r"^news$",
    r"^register$",
    r"^subscribe$",
    r"^menu$",
    r"^search$",
    r"^english$",
    r"^en$",
    r"^kz$",
    r"^ru$",
    r"^facebook$",
    r"^twitter$",
    r"^linkedin$",
    r"^copy link$",
)

MONTH_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Sept|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?"
)

DETAIL_FIELD_DEFAULTS = {
    "Attendance Mode": None,
    "Event Status": None,
    "Online Event URL": None,
    "Price Currency": None,
    "Ticket Availability": None,
    "Speakers": [],
    "Sponsors": [],
    "Contact Email": None,
    "Contact Phone": None,
    "Language": None,
    "Topics": [],
    "Agenda": None,
    "Additional Details": {},
    "Structured Data": None,
    "Detail Text": None,
    "Detail Page Title": None,
    "Detail Page Format": None,
    "Detail Scrape Status": None,
    "Detail Scrape Error": None,
}


@dataclass(frozen=True)
class EventPageConfig:
    key: str
    source: str
    url: str
    category: str = ""
    page_type: str = ""
    status: str = ""
    allowed_domains: Tuple[str, ...] = ()
    allow_url_patterns: Tuple[str, ...] = ()
    deny_url_patterns: Tuple[str, ...] = DEFAULT_DENY_URL_PATTERNS
    deny_title_patterns: Tuple[str, ...] = DEFAULT_DENY_TITLE_PATTERNS
    request_headers: Dict[str, str] = field(default_factory=dict)
    max_events: int = 100
    listing_urls: Tuple[str, ...] = ()


def scrape_event_page(
    config: EventPageConfig,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    """Scrape a configured event page without saving results anywhere."""
    response = _request(config, session=session)
    content_type = response.headers.get("content-type", "")

    if "application/json" in content_type:
        data = response.json()
        return _dedupe_events(_events_from_json_blob(data, config, config.url))

    soup = BeautifulSoup(response.text, "html.parser")
    events = _events_from_json_ld(soup, config)
    events.extend(_events_from_html(soup, config))
    if not events:
        events.extend(_events_from_markdown_alternates(soup, config, session=session))
    return _dedupe_events(events)[: config.max_events]


def enrich_events(
    events: Sequence[Dict[str, Any]],
    source_key: str,
    source_name: str,
) -> List[Dict[str, Any]]:
    """Fill common metadata on existing source-specific scraper output."""
    enriched = []
    for event in events:
        normalized = dict(event)
        normalized.setdefault("Source", source_name)
        if not normalized.get("Event ID"):
            normalized["Event ID"] = _event_id(
                source_key,
                normalized.get("Event URL"),
                normalized.get("Event Name"),
                normalized.get("Start Date"),
            )
        if not normalized.get("Month"):
            normalized["Month"] = _month_from_date(normalized.get("Start Date"))
        enriched.append(normalized)
    return enriched


def enrich_event_details(
    events: Sequence[Dict[str, Any]],
    config: EventPageConfig,
    session: Optional[requests.Session] = None,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    """Follow event URLs and enrich listing records without database writes."""
    client = session or requests.Session()
    close_client = session is None
    enriched = []

    try:
        for index, event in enumerate(events, start=1):
            normalized = _with_detail_defaults(event)
            event_url = _normalize_url(event.get("Event URL"))
            listing_urls = (config.url, *config.listing_urls)
            if not event_url or any(
                _same_url(event_url, listing_url) for listing_url in listing_urls
            ):
                normalized["Detail Scrape Status"] = "skipped_no_detail_url"
                normalized["Detail Scrape Error"] = (
                    "The listing did not provide a separate event detail URL."
                )
                enriched.append(normalized)
                continue

            try:
                print(
                    f"Detail page {index}/{len(events)} ({config.key}): {event_url}"
                )
                details = _extract_event_page_details(
                    event_url,
                    config,
                    client,
                    event,
                )
                enriched.append(_merge_event_details(normalized, details))
            except Exception as exc:
                if not continue_on_error:
                    raise
                normalized["Detail Scrape Status"] = "failed"
                normalized["Detail Scrape Error"] = str(exc)
                enriched.append(normalized)
    finally:
        if close_client:
            client.close()

    return enriched


def _request(
    config: EventPageConfig,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    client = session or requests.Session()
    headers = dict(DEFAULT_HEADERS)
    headers.update(config.request_headers)
    response = client.get(config.url, headers=headers, timeout=30)
    print(f"Response status code ({config.source}): {response.status_code}")
    response.raise_for_status()
    return response


def _events_from_json_ld(
    soup: BeautifulSoup,
    config: EventPageConfig,
) -> List[Dict[str, Any]]:
    events = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        events.extend(_events_from_json_blob(data, config, config.url))
    return events


def _events_from_json_blob(
    data: Any,
    config: EventPageConfig,
    page_url: str,
) -> List[Dict[str, Any]]:
    events = []
    for item in _walk_json(data):
        if not isinstance(item, dict) or not _json_type_is_event(item.get("@type")):
            continue
        event = _event_from_json_ld(item, config, page_url)
        if event:
            events.append(event)
    return events


def _walk_json(value: Any) -> Iterable[Any]:
    if isinstance(value, list):
        for item in value:
            yield from _walk_json(item)
    elif isinstance(value, dict):
        yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk_json(child)


def _json_type_is_event(value: Any) -> bool:
    if isinstance(value, str):
        return value.lower().endswith("event") or value.lower() == "event"
    if isinstance(value, list):
        return any(_json_type_is_event(item) for item in value)
    return False


def _event_from_json_ld(
    item: Dict[str, Any],
    config: EventPageConfig,
    page_url: str,
) -> Optional[Dict[str, Any]]:
    title = _clean_text(item.get("name") or item.get("headline"))
    if not title:
        return None

    event_url = _first_url(item.get("url")) or item.get("@id") or page_url
    event_url = _normalize_url(urljoin(page_url, str(event_url)))

    start_date, start_time, timezone = _parse_datetime_value(item.get("startDate"))
    end_date, end_time, _ = _parse_datetime_value(item.get("endDate"))
    image_url = _first_url(item.get("image"))
    if image_url:
        image_url = urljoin(page_url, image_url)

    organizer_name, organizer_url = _parse_name_url(item.get("organizer"), page_url)
    venue_name, venue_address = _parse_location(item.get("location"))
    summary = _clean_text(item.get("description"))

    return _build_event(
        config=config,
        title=title,
        event_url=event_url,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        timezone=timezone,
        image_url=image_url,
        venue_name=venue_name,
        venue_address=venue_address,
        organizer_name=organizer_name,
        organizer_url=organizer_url,
        summary=summary,
    )


def _events_from_html(
    soup: BeautifulSoup,
    config: EventPageConfig,
) -> List[Dict[str, Any]]:
    events = []
    seen_nodes = set()
    selectors = (
        "[itemtype*='Event']",
        "[typeof*='Event']",
        "article",
        "li",
        ".views-row",
        ".event",
        ".event-card",
        ".event-listing",
        ".event-item",
        ".calendar-item",
        ".card",
        ".listing-item",
        ".teaser",
        ".post",
        ".entry",
        ".result",
        "[class*='event']",
        "[class*='calendar']",
        "[class*='meeting']",
        "[class*='webinar']",
        "[id*='event']",
        "[id*='calendar']",
    )

    for selector in selectors:
        for node in soup.select(selector):
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            event = _event_from_node(node, config)
            if event:
                events.append(event)

    if not events:
        events.extend(_events_from_links(soup, config))

    return events


def _event_from_node(node: Any, config: EventPageConfig) -> Optional[Dict[str, Any]]:
    text = _clean_text(node.get_text(" ", strip=True))
    if not text or len(text) < 8 or len(text) > 3500:
        return None

    anchor = _best_anchor(node, config)
    title = _extract_title(node, anchor)
    if not title or _matches_any(title, config.deny_title_patterns):
        return None

    linked_event_url = _url_from_anchor(anchor, config)
    if linked_event_url and not _url_allowed(linked_event_url, config):
        return None

    start_date, end_date = _extract_date_range(node)
    if not start_date and not linked_event_url:
        return None

    event_url = linked_event_url or config.url
    if not start_date and _same_url(event_url, config.url):
        return None

    if not start_date and not _looks_event_like(node, text, event_url, config):
        return None

    start_time, end_time, timezone = _extract_times(node)
    venue_name, venue_address = _extract_html_location(node)
    image_url = _extract_image_url(node, config.url)
    summary = _extract_summary(node, title)

    return _build_event(
        config=config,
        title=title,
        event_url=event_url,
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        timezone=timezone,
        image_url=image_url,
        venue_name=venue_name,
        venue_address=venue_address,
        summary=summary,
    )


def _events_from_links(
    soup: BeautifulSoup,
    config: EventPageConfig,
) -> List[Dict[str, Any]]:
    events = []
    for anchor in soup.find_all("a", href=True):
        title = _clean_text(anchor.get_text(" ", strip=True))
        if not title or len(title) < 5 or len(title) > 180:
            continue
        if _matches_any(title, config.deny_title_patterns):
            continue
        event_url = _url_from_anchor(anchor, config)
        if not event_url or not _url_allowed(event_url, config):
            continue
        if not _looks_event_like(anchor, title, event_url, config):
            continue

        parent = anchor.find_parent(["article", "li", "div", "section"]) or anchor
        start_date, end_date = _extract_date_range(parent)
        summary = _extract_summary(parent, title)
        events.append(
            _build_event(
                config=config,
                title=title,
                event_url=event_url,
                start_date=start_date,
                end_date=end_date,
                summary=summary,
            )
        )
    return events


def _events_from_markdown_alternates(
    soup: BeautifulSoup,
    config: EventPageConfig,
    session: Optional[requests.Session] = None,
) -> List[Dict[str, Any]]:
    events = []
    alternates = soup.find_all(
        "link",
        attrs={"rel": re.compile("alternate", re.I), "href": True},
    )
    markdown_urls = []
    for alternate in alternates:
        content_type = alternate.get("type", "")
        href = alternate.get("href", "")
        if "markdown" in content_type.lower() or href.endswith(".md"):
            markdown_urls.append(urljoin(config.url, href))

    client = session or requests.Session()
    for markdown_url in markdown_urls:
        if not _url_allowed(markdown_url, config):
            continue
        try:
            response = client.get(markdown_url, headers=DEFAULT_HEADERS, timeout=30)
            print(f"Response status code ({config.source} markdown): {response.status_code}")
            response.raise_for_status()
        except requests.RequestException:
            continue
        events.extend(_events_from_markdown(response.text, markdown_url, config))
    return events


def _events_from_markdown(
    markdown: str,
    markdown_url: str,
    config: EventPageConfig,
) -> List[Dict[str, Any]]:
    events = []
    pattern = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)\s*(?:\(([^)]+)\))?", re.M)
    for match in pattern.finditer(markdown):
        title = _clean_text(match.group(1))
        if not title or _matches_any(title, config.deny_title_patterns):
            continue
        event_url = _markdown_url_to_html(urljoin(markdown_url, match.group(2)))
        if not _url_allowed(event_url, config):
            continue
        start_date, end_date = _parse_date_range_from_text(match.group(3) or "")
        events.append(
            _build_event(
                config=config,
                title=title,
                event_url=event_url,
                start_date=start_date,
                end_date=end_date,
            )
        )
    return events


def _markdown_url_to_html(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path[:-3] if parsed.path.endswith(".md") else parsed.path
    return urlunparse(parsed._replace(path=path))


def _best_anchor(node: Any, config: EventPageConfig) -> Optional[Any]:
    heading = node.find(["h1", "h2", "h3", "h4"])
    if heading:
        heading_anchor = heading.find("a", href=True)
        if heading_anchor and _url_allowed(_url_from_anchor(heading_anchor, config), config):
            return heading_anchor

    anchors = []
    for anchor in node.find_all("a", href=True):
        url = _url_from_anchor(anchor, config)
        if not url or not _url_allowed(url, config):
            continue
        title = _clean_text(anchor.get_text(" ", strip=True))
        if not title or _matches_any(title, config.deny_title_patterns):
            continue
        anchors.append((len(title), anchor))
    if not anchors:
        return None
    anchors.sort(reverse=True, key=lambda item: item[0])
    return anchors[0][1]


def _extract_title(node: Any, anchor: Optional[Any]) -> Optional[str]:
    for heading in node.find_all(["h1", "h2", "h3", "h4"], limit=3):
        text = _clean_text(heading.get_text(" ", strip=True))
        if text and 5 <= len(text) <= 220:
            return text

    if anchor:
        text = _clean_text(anchor.get_text(" ", strip=True))
        if text and 5 <= len(text) <= 220:
            return text
        title_attr = _clean_text(anchor.get("title"))
        if title_attr and 5 <= len(title_attr) <= 220:
            return title_attr
    return None


def _url_from_anchor(anchor: Optional[Any], config: EventPageConfig) -> Optional[str]:
    if not anchor or not anchor.get("href"):
        return None
    href = str(anchor["href"]).strip()
    if not href or href.startswith("#"):
        return None
    return _normalize_url(urljoin(config.url, href))


def _url_allowed(url: Optional[str], config: EventPageConfig) -> bool:
    if not url:
        return False
    if _matches_any(url, config.deny_url_patterns):
        return False
    if config.allow_url_patterns and not _matches_any(url, config.allow_url_patterns):
        return False

    allowed = config.allowed_domains or (_registered_domain(config.url),)
    host = urlparse(url).netloc.lower()
    return any(host == domain or host.endswith("." + domain) for domain in allowed)


def _looks_event_like(
    node: Any,
    text: str,
    event_url: str,
    config: EventPageConfig,
) -> bool:
    haystack = " ".join(
        [
            event_url,
            " ".join(node.get("class", [])) if hasattr(node, "get") else "",
            node.get("id", "") if hasattr(node, "get") else "",
        ]
    ).lower()
    event_terms = (
        "event",
        "calendar",
        "webinar",
        "workshop",
        "conference",
        "meeting",
        "summit",
        "forum",
        "training",
    )
    return any(term in haystack for term in event_terms)


def _extract_date_range(node: Any) -> Tuple[Optional[date], Optional[date]]:
    for time_node in node.find_all("time"):
        value = time_node.get("datetime") or time_node.get("content") or time_node.get_text(" ", strip=True)
        start_date, _, _ = _parse_datetime_value(value)
        if start_date:
            return start_date, None

    text = _clean_text(node.get_text(" ", strip=True))
    return _parse_date_range_from_text(text)


def _parse_date_range_from_text(text: str) -> Tuple[Optional[date], Optional[date]]:
    if not text:
        return None, None

    same_month_range = re.search(
        rf"\b(\d{{1,2}})\s*[-\u2013\u2014]\s*(\d{{1,2}})\s+({MONTH_PATTERN})\s+(20\d{{2}})\b",
        text,
        flags=re.I,
    )
    if same_month_range:
        start = _parse_date(f"{same_month_range.group(1)} {same_month_range.group(3)} {same_month_range.group(4)}")
        end = _parse_date(f"{same_month_range.group(2)} {same_month_range.group(3)} {same_month_range.group(4)}")
        return start, end

    month_first_range = re.search(
        rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\s*[-\u2013\u2014]\s*(\d{{1,2}}),?\s+(20\d{{2}})\b",
        text,
        flags=re.I,
    )
    if month_first_range:
        start = _parse_date(f"{month_first_range.group(1)} {month_first_range.group(2)} {month_first_range.group(4)}")
        end = _parse_date(f"{month_first_range.group(1)} {month_first_range.group(3)} {month_first_range.group(4)}")
        return start, end

    full_range = re.search(
        rf"\b((?:\d{{1,2}}\s+)?(?:{MONTH_PATTERN})\s+\d{{1,2}},?\s+20\d{{2}}|"
        rf"\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+20\d{{2}})"
        rf"\s*(?:-|to|\u2013|\u2014)\s*"
        rf"((?:\d{{1,2}}\s+)?(?:{MONTH_PATTERN})\s+\d{{1,2}},?\s+20\d{{2}}|"
        rf"\d{{1,2}}\s+(?:{MONTH_PATTERN})\s+20\d{{2}})",
        text,
        flags=re.I,
    )
    if full_range:
        return _parse_date(full_range.group(1)), _parse_date(full_range.group(2))

    for pattern in (
        rf"\b\d{{1,2}}\s+({MONTH_PATTERN})\s+20\d{{2}}\b",
        rf"\b({MONTH_PATTERN})\s+\d{{1,2}},?\s+20\d{{2}}\b",
        r"\b20\d{2}[-/]\d{1,2}[-/]\d{1,2}\b",
    ):
        match = re.search(pattern, text, flags=re.I)
        if match:
            return _parse_date(match.group(0)), None

    return None, None


def _parse_date(value: str) -> Optional[date]:
    parsed_date, _, _ = _parse_datetime_value(value)
    return parsed_date


def _parse_datetime_value(value: Any) -> Tuple[Optional[date], Optional[time], Optional[str]]:
    if not value:
        return None, None, None
    if isinstance(value, list):
        return _parse_datetime_value(value[0] if value else None)
    if isinstance(value, dict):
        return _parse_datetime_value(value.get("value") or value.get("@value"))
    if isinstance(value, datetime):
        return value.date(), value.time(), value.tzname()
    if isinstance(value, date):
        return value, None, None

    text = _clean_text(str(value))
    if not text:
        return None, None, None
    try:
        parsed = date_parser.parse(text, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None, None, None

    parsed_time = parsed.time() if parsed.time() != time(0, 0) else None
    timezone = parsed.tzname()
    return parsed.date(), parsed_time, timezone


def _extract_times(node: Any) -> Tuple[Optional[time], Optional[time], Optional[str]]:
    text = _clean_text(node.get_text(" ", strip=True))
    match = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\s*(?:-|to|\u2013|\u2014)\s*"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM))\s*([A-Z]{2,5})?",
        text,
    )
    if not match:
        return None, None, None
    start = _parse_time(match.group(1))
    end = _parse_time(match.group(2))
    timezone = match.group(3)
    return start, end, timezone


def _parse_time(value: str) -> Optional[time]:
    try:
        return date_parser.parse(value).time()
    except (ValueError, TypeError, OverflowError):
        return None


def _extract_html_location(node: Any) -> Tuple[Optional[str], Optional[str]]:
    for selector in ("[class*='venue']", "[class*='location']", "[class*='address']"):
        location_node = node.select_one(selector)
        if location_node:
            text = _clean_text(location_node.get_text(" ", strip=True))
            if text and len(text) <= 300:
                return text, text
    return None, None


def _extract_image_url(node: Any, page_url: str) -> Optional[str]:
    image = node.find("img")
    if not image:
        return None
    src = image.get("src") or image.get("data-src") or image.get("data-lazy-src")
    if not src:
        return None
    return urljoin(page_url, src)


def _extract_summary(node: Any, title: str) -> Optional[str]:
    for paragraph in node.find_all("p", limit=3):
        text = _clean_text(paragraph.get_text(" ", strip=True))
        if text and text != title and len(text) > 20:
            return text[:1000]

    text = _clean_text(node.get_text(" ", strip=True))
    if not text:
        return None
    text = text.replace(title, "", 1).strip()
    return text[:1000] if len(text) > 20 else None


def _parse_location(value: Any) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    if isinstance(value, list):
        return _parse_location(value[0] if value else None)
    if isinstance(value, str):
        text = _clean_text(value)
        return text, text
    if not isinstance(value, dict):
        return None, None

    name = _clean_text(value.get("name"))
    address = value.get("address")
    if isinstance(address, dict):
        address_parts = [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("postalCode"),
            address.get("addressCountry"),
        ]
        address_text = _clean_text(", ".join(str(part) for part in address_parts if part))
    else:
        address_text = _clean_text(address)
    return name, address_text


def _parse_name_url(value: Any, page_url: str) -> Tuple[Optional[str], Optional[str]]:
    if not value:
        return None, None
    if isinstance(value, list):
        return _parse_name_url(value[0] if value else None, page_url)
    if isinstance(value, str):
        return _clean_text(value), None
    if not isinstance(value, dict):
        return None, None
    name = _clean_text(value.get("name"))
    url = _first_url(value.get("url"))
    return name, urljoin(page_url, url) if url else None


def _first_url(value: Any) -> Optional[str]:
    if not value:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            result = _first_url(item)
            if result:
                return result
    if isinstance(value, dict):
        return value.get("url") or value.get("@id")
    return None


def _build_event(
    config: EventPageConfig,
    title: str,
    event_url: Optional[str],
    start_date: Optional[Any] = None,
    end_date: Optional[Any] = None,
    start_time: Optional[Any] = None,
    end_time: Optional[Any] = None,
    timezone: Optional[str] = None,
    image_url: Optional[str] = None,
    ticket_price: Optional[str] = None,
    tickets_url: Optional[str] = None,
    venue_name: Optional[str] = None,
    venue_address: Optional[str] = None,
    organizer_name: Optional[str] = None,
    organizer_url: Optional[str] = None,
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    event_url = _normalize_url(event_url or config.url)
    return {
        "Event Name": title,
        "Event ID": _event_id(config.key, event_url, title, start_date),
        "Event URL": event_url,
        "Start Date": start_date,
        "End Date": end_date,
        "Start Time": start_time,
        "End Time": end_time,
        "Timezone": timezone,
        "Image URL": image_url,
        "Ticket Price": ticket_price,
        "Tickets URL": tickets_url,
        "Venue Name": venue_name,
        "Venue Address": venue_address,
        "Organizer Name": organizer_name,
        "Organizer URL": organizer_url,
        "Summary": summary,
        "Tags": [config.category] if config.category else [],
        "Source": config.source,
        "Month": _month_from_date(start_date),
    }


def _event_id(
    source_key: str,
    event_url: Optional[str],
    title: Optional[str],
    start_date: Optional[Any],
) -> str:
    raw = "|".join(
        str(part or "").strip().lower()
        for part in (source_key, event_url, title, start_date)
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _month_from_date(value: Optional[Any]) -> Optional[str]:
    if isinstance(value, datetime):
        return value.strftime("%B %Y")
    if isinstance(value, date):
        return value.strftime("%B %Y")
    if isinstance(value, str):
        parsed_date, _, _ = _parse_datetime_value(value)
        if parsed_date:
            return parsed_date.strftime("%B %Y")
    return None


def _dedupe_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    deduped = []
    for event in events:
        title = _clean_text(event.get("Event Name"))
        if not title:
            continue
        key = (
            _normalize_url(event.get("Event URL") or ""),
            title.lower(),
            str(event.get("Start Date") or ""),
            event.get("Source"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def _matches_any(value: Optional[str], patterns: Sequence[str]) -> bool:
    if not value:
        return False
    return any(re.search(pattern, value, flags=re.I) for pattern in patterns)


def _registered_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _normalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    parsed = urlparse(url)
    parsed = parsed._replace(fragment="")
    if parsed.scheme in ("http", "https"):
        parsed = parsed._replace(netloc=parsed.netloc.lower())
    return urlunparse(parsed)


def _same_url(first: Optional[str], second: Optional[str]) -> bool:
    return _normalize_url(first) == _normalize_url(second)


def _clean_text(value: Optional[Any]) -> Optional[str]:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def _with_detail_defaults(event: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(event)
    for key, default in DETAIL_FIELD_DEFAULTS.items():
        if key in normalized:
            continue
        if isinstance(default, list):
            normalized[key] = list(default)
        elif isinstance(default, dict):
            normalized[key] = dict(default)
        else:
            normalized[key] = default
    return normalized


def _extract_event_page_details(
    event_url: str,
    config: EventPageConfig,
    session: requests.Session,
    listing_event: Dict[str, Any],
) -> Dict[str, Any]:
    content_format, payload = _fetch_detail_content(event_url, config, session)

    if content_format == "json":
        candidates = [
            item
            for item in _walk_json(payload)
            if isinstance(item, dict) and _json_type_is_event(item.get("@type"))
        ]
        item = _best_structured_event(candidates, listing_event, event_url)
        details = _details_from_json_event(item, event_url) if item else {}
    elif content_format == "html":
        details = _details_from_html(str(payload), event_url, listing_event)
    else:
        details = _details_from_markdown(str(payload), event_url)

    details["Detail Page Format"] = content_format
    details["Detail Scrape Status"] = "ok"
    details["Detail Scrape Error"] = None
    return details


def _fetch_detail_content(
    event_url: str,
    config: EventPageConfig,
    session: requests.Session,
) -> Tuple[str, Any]:
    parsed = urlparse(event_url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsupported event detail URL: {event_url}")

    headers = dict(DEFAULT_HEADERS)
    headers.update(config.request_headers)
    direct_error = None
    try:
        response = session.get(event_url, headers=headers, timeout=30)
        if response.ok and not _looks_like_block_page(response.text):
            content_type = response.headers.get("content-type", "").lower()
            if "application/json" in content_type:
                return "json", response.json()
            return "html", response.text
        direct_error = f"HTTP {response.status_code}"
    except requests.RequestException as exc:
        direct_error = str(exc)

    reader_url = f"https://r.jina.ai/{event_url}"
    reader_headers = {
        "accept": "text/plain",
        "user-agent": DEFAULT_HEADERS["user-agent"],
    }
    try:
        response = session.get(reader_url, headers=reader_headers, timeout=45)
        response.raise_for_status()
        if not response.text.strip():
            raise ValueError("Reader returned an empty response")
        if _looks_like_block_page(response.text):
            raise ValueError("Reader returned an access-denied page")
        return "markdown_reader", response.text
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(
            f"Direct detail request failed ({direct_error}); reader fallback failed ({exc})"
        ) from exc


def _looks_like_block_page(value: str) -> bool:
    sample = value[:10000].lower()
    indicators = (
        "just a moment...",
        "attention required! | cloudflare",
        "cf-chl-",
        "enable javascript and cookies to continue",
        "captcha challenge",
        "access denied",
        "you don't have permission to access",
        "errors.edgesuite.net",
    )
    return any(indicator in sample for indicator in indicators)


def _details_from_html(
    html: str,
    page_url: str,
    listing_event: Dict[str, Any],
) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    structured_candidates = []
    for script in soup.find_all("script", attrs={"type": re.compile("ld\\+json", re.I)}):
        raw = script.string or script.get_text(" ", strip=True)
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        structured_candidates.extend(
            item
            for item in _walk_json(data)
            if isinstance(item, dict) and _json_type_is_event(item.get("@type"))
        )

    item = _best_structured_event(
        structured_candidates,
        listing_event,
        page_url,
    )
    details = _details_from_json_event(item, page_url) if item else {}
    html_details = _details_from_visible_html(soup, page_url)
    return _merge_detail_sources(details, html_details)


def _best_structured_event(
    candidates: Sequence[Dict[str, Any]],
    listing_event: Dict[str, Any],
    page_url: str,
) -> Optional[Dict[str, Any]]:
    if not candidates:
        return None

    target_title = _identity_text(listing_event.get("Event Name"))
    target_url = _normalize_url(listing_event.get("Event URL") or page_url)

    def score(item: Dict[str, Any]) -> float:
        item_title = _identity_text(item.get("name") or item.get("headline"))
        item_url = _normalize_url(
            urljoin(page_url, str(_first_url(item.get("url")) or item.get("@id") or ""))
        )
        value = 0.0
        if target_url and item_url and _same_url(target_url, item_url):
            value += 100.0
        if target_title and item_title:
            if target_title == item_title:
                value += 100.0
            elif target_title in item_title or item_title in target_title:
                value += 50.0
            target_words = set(target_title.split())
            item_words = set(item_title.split())
            if target_words and item_words:
                value += 30.0 * len(target_words & item_words) / len(target_words | item_words)
        return value

    return max(candidates, key=score)


def _details_from_json_event(
    item: Optional[Dict[str, Any]],
    page_url: str,
) -> Dict[str, Any]:
    if not item:
        return {}

    start_date, start_time, timezone = _parse_datetime_value(item.get("startDate"))
    end_date, end_time, end_timezone = _parse_datetime_value(item.get("endDate"))
    organizer_name, organizer_url = _parse_name_url(item.get("organizer"), page_url)
    venue_name, venue_address, online_url = _parse_detail_locations(
        item.get("location"),
        page_url,
    )
    offer = _first_mapping(item.get("offers"))
    price_specification = _first_mapping(offer.get("priceSpecification")) if offer else None
    price = None
    currency = None
    tickets_url = None
    availability = None
    if offer:
        price = offer.get("price")
        currency = offer.get("priceCurrency")
        tickets_url = _first_url(offer.get("url"))
        availability = _schema_label(offer.get("availability"))
    if price_specification:
        if price is None:
            price = price_specification.get("price")
        currency = currency or price_specification.get("priceCurrency")

    organizer = _first_mapping(item.get("organizer"))
    contact_point = _first_mapping(organizer.get("contactPoint")) if organizer else None
    contact_email = item.get("email")
    contact_phone = item.get("telephone")
    if contact_point:
        contact_email = contact_email or contact_point.get("email")
        contact_phone = contact_phone or contact_point.get("telephone")

    additional = {}
    for source_key, output_key in (
        ("audience", "Audience"),
        ("duration", "Duration"),
        ("doorTime", "Door Time"),
        ("previousStartDate", "Previous Start Date"),
        ("typicalAgeRange", "Age Range"),
        ("maximumAttendeeCapacity", "Maximum Attendee Capacity"),
        ("remainingAttendeeCapacity", "Remaining Attendee Capacity"),
    ):
        value = item.get(source_key)
        if value is not None:
            additional[output_key] = _structured_value_text(value)

    image_url = _first_url(item.get("image"))
    return {
        "Event Name": _clean_text(item.get("name") or item.get("headline")),
        "Start Date": start_date,
        "End Date": end_date,
        "Start Time": start_time,
        "End Time": end_time,
        "Timezone": timezone or end_timezone,
        "Image URL": urljoin(page_url, image_url) if image_url else None,
        "Ticket Price": _clean_text(price),
        "Tickets URL": urljoin(page_url, tickets_url) if tickets_url else None,
        "Venue Name": venue_name,
        "Venue Address": venue_address,
        "Organizer Name": organizer_name,
        "Organizer URL": organizer_url,
        "Summary": _clean_text(item.get("description") or item.get("abstract")),
        "Attendance Mode": _schema_label(item.get("eventAttendanceMode")),
        "Event Status": _schema_label(item.get("eventStatus")),
        "Online Event URL": online_url,
        "Price Currency": _clean_text(currency),
        "Ticket Availability": availability,
        "Speakers": _names_from_structured_values(
            item.get("performer"),
            item.get("actor"),
            item.get("speaker"),
        ),
        "Sponsors": _names_from_structured_values(
            item.get("sponsor"),
            item.get("funder"),
        ),
        "Contact Email": _clean_text(contact_email),
        "Contact Phone": _clean_text(contact_phone),
        "Language": _structured_value_text(item.get("inLanguage")),
        "Topics": _text_list(item.get("keywords") or item.get("about")),
        "Additional Details": additional,
        "Structured Data": item,
    }


def _details_from_visible_html(
    soup: BeautifulSoup,
    page_url: str,
) -> Dict[str, Any]:
    root = soup.select_one(
        "main, article, [role='main'], .event-detail, .event-content, .single-event"
    ) or soup.body or soup
    detail_text = _visible_detail_text(root)

    page_title = _meta_content(soup, "property", "og:title")
    if not page_title:
        heading = root.find(["h1", "h2"])
        page_title = _clean_text(heading.get_text(" ", strip=True)) if heading else None
    page_title = page_title or _clean_text(soup.title.string if soup.title else None)

    summary = (
        _meta_content(soup, "property", "og:description")
        or _meta_content(soup, "name", "description")
        or _first_substantial_paragraph(root)
    )
    image_url = (
        _meta_content(soup, "property", "og:image")
        or _meta_content(soup, "name", "twitter:image")
        or _extract_image_url(root, page_url)
    )
    if image_url:
        image_url = urljoin(page_url, image_url)

    start_date, end_date, start_time, end_time, timezone = _html_date_time_details(
        root,
        detail_text,
    )
    venue_name, venue_address = _extract_html_location(root)
    organizer_name, organizer_url = _html_named_link(
        root,
        page_url,
        ("organizer", "organiser", "host"),
    )
    tickets_url = _registration_url(root, page_url)
    email, phone = _contact_details(root, detail_text)
    additional = _html_labeled_details(root)
    topics = _text_list(_meta_content(soup, "name", "keywords"))
    agenda_node = root.select_one("#agenda, [class*='agenda'], [id*='programme']")
    agenda = (
        _clean_text(agenda_node.get_text(" ", strip=True))[:10000]
        if agenda_node and _clean_text(agenda_node.get_text(" ", strip=True))
        else None
    )

    attendance_mode = None
    mode_text = " ".join(
        value for value in (page_title, summary, detail_text[:1500]) if value
    )
    if re.search(r"\b(online|virtual|webinar|webcast)\b", mode_text, re.I):
        attendance_mode = "Online"
    if attendance_mode and re.search(r"\b(in person|in-person|onsite|on-site)\b", mode_text, re.I):
        attendance_mode = "Mixed"

    return {
        "Event Name": page_title,
        "Start Date": start_date,
        "End Date": end_date,
        "Start Time": start_time,
        "End Time": end_time,
        "Timezone": timezone,
        "Image URL": image_url,
        "Tickets URL": tickets_url,
        "Venue Name": venue_name,
        "Venue Address": venue_address,
        "Organizer Name": organizer_name,
        "Organizer URL": organizer_url,
        "Summary": summary,
        "Attendance Mode": attendance_mode,
        "Online Event URL": tickets_url if attendance_mode == "Online" else None,
        "Speakers": _html_people(root, ("speaker", "presenter", "panelist")),
        "Sponsors": _html_people(root, ("sponsor", "partner", "supporter")),
        "Contact Email": email,
        "Contact Phone": phone,
        "Topics": topics,
        "Agenda": agenda,
        "Additional Details": additional,
        "Detail Text": detail_text,
        "Detail Page Title": page_title,
    }


def _details_from_markdown(markdown: str, page_url: str) -> Dict[str, Any]:
    title_match = re.search(r"^Title:\s*(.+)$", markdown, flags=re.I | re.M)
    content = markdown.split("Markdown Content:", 1)[-1]
    heading_match = re.search(r"^#\s+(.+)$", content, flags=re.M)
    page_title = _clean_text(
        title_match.group(1) if title_match else heading_match.group(1) if heading_match else None
    )
    content = _scope_markdown_event_content(content, page_title)
    detail_text = _markdown_to_text(content)
    start_date, end_date = _parse_date_range_from_text(detail_text)
    start_time, end_time, timezone = _extract_times_from_text(detail_text)
    additional = _markdown_labeled_details(content)

    venue = _additional_detail(additional, ("venue", "location", "address"))
    organizer = _additional_detail(additional, ("organizer", "organiser", "host"))
    image_match = re.search(r"!\[[^\]]*\]\(([^\s)]+)", content)
    tickets_url = None
    for label, target in re.findall(r"\[([^\]]+)\]\(([^\s)]+)", content):
        if re.search(
            r"\b(register|registration|tickets?|book|rsvp|attend|join now)\b",
            label,
            re.I,
        ):
            tickets_url = urljoin(page_url, target)
            break

    email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", detail_text, re.I)
    phone_match = re.search(
        r"(?:phone|telephone|tel)\s*:?\s*(\+?[0-9][0-9 ()-]{6,}[0-9])",
        detail_text,
        re.I,
    )
    attendance_mode = None
    summary = _markdown_summary(content, page_title)
    mode_text = " ".join(
        value for value in (page_title, summary, detail_text[:1500]) if value
    )
    if re.search(r"\b(online|virtual|webinar|webcast)\b", mode_text, re.I):
        attendance_mode = "Online"
    if attendance_mode and re.search(r"\b(in person|in-person|onsite|on-site)\b", mode_text, re.I):
        attendance_mode = "Mixed"

    return {
        "Event Name": page_title,
        "Start Date": start_date,
        "End Date": end_date,
        "Start Time": start_time,
        "End Time": end_time,
        "Timezone": timezone,
        "Image URL": urljoin(page_url, image_match.group(1)) if image_match else None,
        "Tickets URL": tickets_url,
        "Venue Name": venue,
        "Venue Address": venue,
        "Organizer Name": organizer,
        "Summary": summary,
        "Attendance Mode": attendance_mode,
        "Online Event URL": tickets_url if attendance_mode == "Online" else None,
        "Contact Email": email_match.group(0) if email_match else None,
        "Contact Phone": phone_match.group(1) if phone_match else None,
        "Agenda": _markdown_agenda(content),
        "Additional Details": additional,
        "Detail Text": detail_text,
        "Detail Page Title": page_title,
    }


def _merge_event_details(
    event: Dict[str, Any],
    details: Dict[str, Any],
) -> Dict[str, Any]:
    merged = _with_detail_defaults(event)
    identity_fields = {"Event ID", "Event URL", "Source", "Tags"}
    list_fields = {"Speakers", "Sponsors", "Topics"}

    for key, value in details.items():
        if key in identity_fields or value in (None, "", [], {}):
            continue
        if key in list_fields:
            merged[key] = _unique_texts(
                list(merged.get(key) or []) + list(value or [])
            )
        elif key == "Additional Details":
            combined = dict(merged.get(key) or {})
            combined.update(value)
            merged[key] = combined
        elif key == "Event Name" and merged.get("Event Name"):
            continue
        elif key == "Summary" and merged.get("Summary"):
            existing = str(merged["Summary"])
            if len(str(value)) > len(existing):
                merged[key] = value
        else:
            merged[key] = value

    merged["Detail Scrape Status"] = details.get("Detail Scrape Status", "ok")
    merged["Detail Scrape Error"] = details.get("Detail Scrape Error")
    merged["Month"] = _month_from_date(merged.get("Start Date"))
    return merged


def _merge_detail_sources(
    primary: Dict[str, Any],
    fallback: Dict[str, Any],
) -> Dict[str, Any]:
    merged = dict(primary)
    for key, value in fallback.items():
        if value in (None, "", [], {}):
            continue
        if key == "Additional Details":
            combined = dict(value)
            combined.update(primary.get(key) or {})
            merged[key] = combined
        elif key in {"Speakers", "Sponsors", "Topics"}:
            merged[key] = _unique_texts(
                list(primary.get(key) or []) + list(value or [])
            )
        elif not merged.get(key):
            merged[key] = value

    detail_text = merged.get("Detail Text")
    if detail_text and _looks_like_template_text(str(detail_text)):
        summary_text = _html_fragment_text(
            primary.get("Summary") or fallback.get("Summary")
        )
        if summary_text:
            merged["Detail Text"] = summary_text
    return merged


def _looks_like_template_text(value: str) -> bool:
    normalized = value.lower()
    markers = (
        "text goes here",
        "speaker bio template",
        "partner modal template",
        "agenda bio modal template",
    )
    return sum(marker in normalized for marker in markers) >= 2


def _html_fragment_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return _clean_text(
        BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True)
    )


def _parse_detail_locations(
    value: Any,
    page_url: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    locations = value if isinstance(value, list) else [value]
    venue_name = None
    venue_address = None
    online_url = None
    for location in locations:
        if isinstance(location, dict):
            location_type = str(location.get("@type") or "").lower()
            if "virtual" in location_type:
                target = _first_url(location.get("url")) or location.get("url")
                if target:
                    online_url = urljoin(page_url, str(target))
                continue
        current_name, current_address = _parse_location(location)
        venue_name = venue_name or current_name
        venue_address = venue_address or current_address
    return venue_name, venue_address, online_url


def _first_mapping(value: Any) -> Optional[Dict[str, Any]]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), None)
    return None


def _schema_label(value: Any) -> Optional[str]:
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    text = str(value).rstrip("/").rsplit("/", 1)[-1].rsplit("#", 1)[-1]
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    for suffix in (" Event Attendance Mode", " Event Status"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if text.startswith("Event "):
        text = text[6:]
    return _clean_text(text)


def _names_from_structured_values(*values: Any) -> List[str]:
    names = []
    for value in values:
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                name = item.get("name")
            else:
                name = item
            clean_name = _clean_text(name)
            if clean_name:
                names.append(clean_name)
    return _unique_texts(names)


def _text_list(value: Any) -> List[str]:
    if not value:
        return []
    values = value if isinstance(value, list) else [value]
    output = []
    for item in values:
        if isinstance(item, dict):
            item = item.get("name") or item.get("headline")
        if not item:
            continue
        output.extend(re.split(r"[,;|]", str(item)))
    return _unique_texts(output)


def _structured_value_text(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return _clean_text(value.get("name") or value.get("description") or value.get("@id"))
    if isinstance(value, list):
        values = [_structured_value_text(item) for item in value]
        return _clean_text(", ".join(item for item in values if item))
    return _clean_text(value)


def _identity_text(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _unique_texts(values: Sequence[Any]) -> List[str]:
    seen = set()
    output = []
    for value in values:
        clean_value = _clean_text(value)
        if not clean_value:
            continue
        identity = clean_value.lower()
        if identity in seen:
            continue
        seen.add(identity)
        output.append(clean_value)
    return output


def _meta_content(soup: BeautifulSoup, attribute: str, value: str) -> Optional[str]:
    node = soup.find("meta", attrs={attribute: re.compile(f"^{re.escape(value)}$", re.I)})
    return _clean_text(node.get("content")) if node else None


def _visible_detail_text(root: Any) -> str:
    clone = BeautifulSoup(str(root), "html.parser")
    for node in clone.find_all(
        ["script", "style", "noscript", "template", "svg", "nav", "footer", "form"]
    ):
        node.decompose()
    return (_clean_text(clone.get_text(" ", strip=True)) or "")[:30000]


def _first_substantial_paragraph(root: Any) -> Optional[str]:
    for paragraph in root.find_all("p"):
        text = _clean_text(paragraph.get_text(" ", strip=True))
        if text and len(text) >= 40:
            return text[:5000]
    return None


def _html_date_time_details(
    root: Any,
    detail_text: str,
) -> Tuple[Optional[date], Optional[date], Optional[time], Optional[time], Optional[str]]:
    start_date = end_date = None
    start_time = end_time = None
    timezone = None

    start_node = root.select_one(
        "[itemprop='startDate'], [property='startDate'], [class*='start-date']"
    )
    end_node = root.select_one(
        "[itemprop='endDate'], [property='endDate'], [class*='end-date']"
    )
    if start_node:
        value = (
            start_node.get("datetime")
            or start_node.get("content")
            or start_node.get_text(" ", strip=True)
        )
        start_date, start_time, timezone = _parse_datetime_value(value)
    if end_node:
        value = (
            end_node.get("datetime")
            or end_node.get("content")
            or end_node.get_text(" ", strip=True)
        )
        end_date, end_time, end_timezone = _parse_datetime_value(value)
        timezone = timezone or end_timezone

    if not start_date:
        date_node = root.select_one("[class*='event-date'], [class*='date'], time")
        date_text = (
            _clean_text(date_node.get_text(" ", strip=True)) if date_node else detail_text
        )
        start_date, end_date = _parse_date_range_from_text(date_text or detail_text)
    if not start_time:
        start_time, end_time, text_timezone = _extract_times_from_text(detail_text)
        timezone = timezone or text_timezone
    return start_date, end_date, start_time, end_time, timezone


def _extract_times_from_text(
    text: str,
) -> Tuple[Optional[time], Optional[time], Optional[str]]:
    if not text:
        return None, None, None
    range_match = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*(?:-|to|\u2013|\u2014)\s*"
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*([A-Z]{2,6})?\b",
        text,
        re.I,
    )
    if not range_match:
        range_match = re.search(
            r"\b([01]?\d|2[0-3]):([0-5]\d)\s*(?:-|to|\u2013|\u2014)\s*"
            r"([01]?\d|2[0-3]):([0-5]\d)\s*([A-Z]{2,6})?\b",
            text,
        )
        if range_match:
            start = _parse_time(f"{range_match.group(1)}:{range_match.group(2)}")
            end = _parse_time(f"{range_match.group(3)}:{range_match.group(4)}")
            return start, end, range_match.group(5)
    if range_match:
        return (
            _parse_time(range_match.group(1)),
            _parse_time(range_match.group(2)),
            range_match.group(3),
        )

    single_match = re.search(
        r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*([A-Z]{2,6})?\b",
        text,
        re.I,
    )
    if single_match:
        return _parse_time(single_match.group(1)), None, single_match.group(2)
    return None, None, None


def _html_named_link(
    root: Any,
    page_url: str,
    class_terms: Sequence[str],
) -> Tuple[Optional[str], Optional[str]]:
    selector = ", ".join(f"[class*='{term}']" for term in class_terms)
    node = root.select_one(selector)
    if not node:
        return None, None
    link = node.find("a", href=True)
    name_node = node.select_one("[class*='name'], h2, h3, h4")
    name = _clean_text(
        name_node.get_text(" ", strip=True)
        if name_node
        else link.get_text(" ", strip=True)
        if link
        else node.get_text(" ", strip=True)
    )
    url = urljoin(page_url, link.get("href")) if link else None
    return name[:300] if name else None, url


def _registration_url(root: Any, page_url: str) -> Optional[str]:
    for anchor in root.find_all("a", href=True):
        label = _clean_text(anchor.get_text(" ", strip=True)) or ""
        href = str(anchor.get("href") or "")
        if re.search(
            r"\b(register|registration|tickets?|book|rsvp|attend|join now)\b",
            label,
            re.I,
        ) or re.search(r"/(register|registration|tickets?|rsvp)(?:[/\-?]|$)", href, re.I):
            return urljoin(page_url, href)
    return None


def _contact_details(root: Any, text: str) -> Tuple[Optional[str], Optional[str]]:
    email_link = root.find("a", href=re.compile(r"^mailto:", re.I))
    phone_link = root.find("a", href=re.compile(r"^tel:", re.I))
    email = None
    phone = None
    if email_link:
        email = str(email_link.get("href")).split(":", 1)[-1].split("?", 1)[0]
    if phone_link:
        phone = str(phone_link.get("href")).split(":", 1)[-1]
    if not email:
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.I)
        email = match.group(0) if match else None
    if not phone:
        match = re.search(
            r"(?:phone|telephone|tel)\s*:?\s*(\+?[0-9][0-9 ()-]{6,}[0-9])",
            text,
            re.I,
        )
        phone = match.group(1) if match else None
    return _clean_text(email), _clean_text(phone)


def _html_labeled_details(root: Any) -> Dict[str, str]:
    details = {}
    for term in root.find_all("dt", limit=100):
        description = term.find_next_sibling("dd")
        _add_labeled_detail(
            details,
            term.get_text(" ", strip=True),
            description.get_text(" ", strip=True) if description else None,
        )
    for row in root.find_all("tr", limit=100):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            _add_labeled_detail(
                details,
                cells[0].get_text(" ", strip=True),
                cells[1].get_text(" ", strip=True),
            )
    return details


def _add_labeled_detail(
    details: Dict[str, str],
    label: Any,
    value: Any,
) -> None:
    clean_label = (_clean_text(label) or "").rstrip(":")
    clean_value = _clean_text(value)
    if not clean_label or not clean_value or len(clean_label) > 80:
        return
    details.setdefault(clean_label, clean_value[:2000])


def _html_people(root: Any, class_terms: Sequence[str]) -> List[str]:
    names = []
    selector = ", ".join(f"[class*='{term}']" for term in class_terms)
    for container in root.select(selector)[:100]:
        name_nodes = container.select("[class*='name'], h2, h3, h4")
        if not name_nodes:
            image = container.find("img", alt=True)
            if image:
                names.append(image.get("alt"))
            continue
        for node in name_nodes[:10]:
            name = _clean_text(node.get_text(" ", strip=True))
            if name and 2 <= len(name) <= 200:
                names.append(name)
    return _unique_texts(names)


def _markdown_labeled_details(markdown: str) -> Dict[str, str]:
    details = {}
    lines = markdown.splitlines()
    pattern = re.compile(
        r"^\s*(?:[-*]\s+)?(?:\*\*)?([A-Za-z][A-Za-z0-9 /&()_-]{1,79}?)(?:\*\*)?\s*:\s*(.*)$"
    )
    ignored = {"title", "url source", "published time", "markdown content"}
    common_labels = {
        "date",
        "time",
        "location",
        "venue",
        "address",
        "organizer",
        "organiser",
        "host",
        "format",
        "language",
        "price",
        "cost",
        "registration deadline",
        "audience",
        "contact",
        "email",
        "phone",
    }
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        label = match.group(1).strip()
        label_key = label.lower()
        is_bold_label = bool(re.match(r"^\s*(?:[-*]\s+)?\*\*", line))
        is_short_label = len(label) <= 40 and len(label.split()) <= 6
        if label_key in ignored or not (
            is_bold_label or is_short_label or label_key in common_labels
        ):
            continue
        value = match.group(2).strip().strip("*")
        if not value:
            for following in lines[index + 1 : index + 4]:
                if following.strip():
                    value = following.strip().strip("*")
                    break
        _add_labeled_detail(details, label, _markdown_to_text(value))
    return details


def _additional_detail(
    details: Dict[str, str],
    terms: Sequence[str],
) -> Optional[str]:
    for key, value in details.items():
        if any(term in key.lower() for term in terms):
            return value
    return None


def _markdown_to_text(markdown: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return (_clean_text(text) or "")[:30000]


def _scope_markdown_event_content(
    markdown: str,
    page_title: Optional[str],
) -> str:
    scoped = markdown
    target = _identity_text(page_title)
    if target:
        for match in re.finditer(r"^#{1,6}\s+(.+)$", markdown, flags=re.M):
            heading = _identity_text(match.group(1))
            if heading == target or (len(target) >= 20 and target in heading):
                scoped = markdown[match.start() :]
                break

    footer_patterns = (
        r"\n#{1,6}\s+subscribe\b",
        r"\n\[?subscribe\]?\([^\n]*\)",
        r"\nsubscribe to our mailing list\b",
    )
    end_positions = []
    for pattern in footer_patterns:
        match = re.search(pattern, scoped, flags=re.I)
        if match:
            end_positions.append(match.start())
    if end_positions:
        scoped = scoped[: min(end_positions)]
    return scoped


def _markdown_named_section(
    markdown: str,
    section_names: Sequence[str],
) -> Optional[str]:
    names = "|".join(re.escape(name) for name in section_names)
    match = re.search(
        rf"^([#]{{1,6}})\s+(?:{names})\s*$",
        markdown,
        flags=re.I | re.M,
    )
    if not match:
        return None
    level = len(match.group(1))
    following_heading = re.search(
        rf"^#{{1,{level}}}\s+.+$",
        markdown[match.end() :],
        flags=re.M,
    )
    end = (
        match.end() + following_heading.start()
        if following_heading
        else len(markdown)
    )
    section = _markdown_to_text(markdown[match.end() : end])
    return section[:10000] if section else None


def _markdown_agenda(markdown: str) -> Optional[str]:
    section = _markdown_named_section(
        markdown,
        ("agenda", "programme", "program"),
    )
    if section:
        section = re.split(
            r"\b(?:supporters|sponsors|speakers|venue|partners)\b",
            section,
            maxsplit=1,
            flags=re.I,
        )[0]
        if re.search(r"\b\d{1,2}(?::\d{2})?\b", section):
            return section[:10000]

    plain = _markdown_to_text(markdown)
    match = re.search(
        r"\b(?:agenda|programme|program)\b\s+(?=\d{1,2}(?::\d{2}))",
        plain,
        re.I,
    )
    if not match:
        return None
    agenda = re.split(
        r"\b(?:supporters|sponsors|speakers|venue|partners)\b",
        plain[match.end() :],
        maxsplit=1,
        flags=re.I,
    )[0]
    return (_clean_text(agenda) or "")[:10000] or None


def _markdown_summary(markdown: str, page_title: Optional[str]) -> Optional[str]:
    plain = _markdown_to_text(markdown)
    introduction_matches = list(
        re.finditer(
            r"\b(?:introduction|overview|about the event|event description)\b",
            plain,
            re.I,
        )
    )
    if introduction_matches:
        candidate = plain[introduction_matches[-1].end() :]
        candidate = re.split(
            r"\b(?:agenda|programme|program)\b\s+(?=\d{1,2}(?::\d{2}))|"
            r"\b(?:supporters|sponsors|speakers|venue)\b",
            candidate,
            maxsplit=1,
            flags=re.I,
        )[0]
        candidate = _clean_text(candidate)
        if candidate and len(candidate) >= 40:
            return candidate[:3000]

    introduction = _markdown_named_section(
        markdown,
        ("introduction", "about", "overview", "description"),
    )
    if introduction and len(introduction) >= 40:
        return introduction[:3000]

    paragraphs = []
    for block in re.split(r"\n\s*\n", markdown):
        clean_block = _markdown_to_text(block)
        if not clean_block or clean_block == page_title:
            continue
        if re.match(
            r"^(date|time|location|venue|address|share|home)\s*:",
            clean_block,
            re.I,
        ):
            continue
        if len(clean_block) < 40 or clean_block.lower().startswith(("image", "skip to")):
            continue
        paragraphs.append(clean_block)
        if sum(len(item) for item in paragraphs) >= 3000:
            break
    return _clean_text(" ".join(paragraphs))[:3000] if paragraphs else None
