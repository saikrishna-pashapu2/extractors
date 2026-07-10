import json
import unittest

import requests

from events.event_page_scraper import EventPageConfig, enrich_event_details


class FakeResponse:
    def __init__(self, text, status_code=200, content_type="text/html"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.responses.pop(0)


class EventDetailScraperTests(unittest.TestCase):
    def setUp(self):
        self.config = EventPageConfig(
            key="example",
            source="Example Events",
            url="https://example.com/events",
            category="Sustainable finance",
        )
        self.listing_event = {
            "Event Name": "Transition Finance Forum",
            "Event ID": "stable-id",
            "Event URL": "https://example.com/events/transition-finance-forum",
            "Start Date": None,
            "End Date": None,
            "Start Time": None,
            "End Time": None,
            "Timezone": None,
            "Image URL": None,
            "Ticket Price": None,
            "Tickets URL": None,
            "Venue Name": None,
            "Venue Address": None,
            "Organizer Name": None,
            "Organizer URL": None,
            "Summary": None,
            "Tags": ["Sustainable finance"],
            "Source": "Example Events",
            "Month": None,
        }

    def test_enriches_json_ld_and_visible_detail_content(self):
        structured_event = {
            "@context": "https://schema.org",
            "@type": "Event",
            "name": "Transition Finance Forum",
            "startDate": "2026-08-22T09:30:00+04:00",
            "endDate": "2026-08-22T16:30:00+04:00",
            "description": "A detailed forum for transition-finance practitioners.",
            "image": "https://example.com/forum.jpg",
            "eventAttendanceMode": "https://schema.org/MixedEventAttendanceMode",
            "eventStatus": "https://schema.org/EventScheduled",
            "location": [
                {
                    "@type": "Place",
                    "name": "Finance Centre",
                    "address": {
                        "streetAddress": "1 Market Street",
                        "addressLocality": "Dubai",
                        "addressCountry": "UAE",
                    },
                },
                {
                    "@type": "VirtualLocation",
                    "url": "https://stream.example.com/forum",
                },
            ],
            "organizer": {
                "@type": "Organization",
                "name": "Example Institute",
                "url": "https://example.com",
                "contactPoint": {
                    "email": "events@example.com",
                    "telephone": "+971 4 555 0100",
                },
            },
            "offers": {
                "@type": "Offer",
                "price": "75",
                "priceCurrency": "USD",
                "url": "https://example.com/register/forum",
                "availability": "https://schema.org/InStock",
            },
            "performer": [{"@type": "Person", "name": "Alex Morgan"}],
            "sponsor": [{"@type": "Organization", "name": "Green Bank"}],
            "inLanguage": "English",
            "keywords": "transition finance, climate risk",
        }
        html = f"""
        <html><head>
          <title>Transition Finance Forum</title>
          <script type="application/ld+json">{json.dumps(structured_event)}</script>
        </head><body><main>
          <h1>Transition Finance Forum</h1>
          <section id="agenda"><h2>Agenda</h2><p>09:30 Opening keynote</p></section>
          <dl><dt>Audience</dt><dd>Investors and policymakers</dd></dl>
        </main></body></html>
        """
        session = FakeSession([FakeResponse(html)])

        event = enrich_event_details(
            [self.listing_event],
            self.config,
            session=session,
        )[0]

        self.assertEqual(event["Event ID"], "stable-id")
        self.assertEqual(event["Start Date"].isoformat(), "2026-08-22")
        self.assertEqual(event["Start Time"].isoformat(), "09:30:00")
        self.assertEqual(event["Attendance Mode"], "Mixed")
        self.assertEqual(event["Event Status"], "Scheduled")
        self.assertEqual(event["Venue Name"], "Finance Centre")
        self.assertEqual(event["Tickets URL"], "https://example.com/register/forum")
        self.assertEqual(event["Ticket Price"], "75")
        self.assertEqual(event["Speakers"], ["Alex Morgan"])
        self.assertEqual(event["Sponsors"], ["Green Bank"])
        self.assertEqual(event["Topics"], ["transition finance", "climate risk"])
        self.assertIn("Opening keynote", event["Agenda"])
        self.assertEqual(event["Additional Details"]["Audience"], "Investors and policymakers")
        self.assertEqual(event["Detail Scrape Status"], "ok")
        self.assertEqual(event["Detail Page Format"], "html")
        self.assertIsNotNone(event["Structured Data"])

    def test_reader_fallback_extracts_scoped_markdown_details(self):
        markdown = """Title: Climate Forum 2026
URL Source: https://example.com/events/climate-forum
Markdown Content:
# Climate Forum 2026

**Date:** 9 September 2026
**Time:** 11:30 - 17:00
**Location:** City Conference Centre

## Introduction
The forum brings investors and policymakers together to finance resilient infrastructure.

## Agenda
11:30 - Opening
12:00 - Investor panel

## Venue
City Conference Centre is next to the metro station.

[Join our network](https://example.com/network)
"""
        session = FakeSession(
            [
                FakeResponse("Access denied", status_code=403),
                FakeResponse(markdown, content_type="text/plain"),
            ]
        )

        event = enrich_event_details(
            [
                {
                    **self.listing_event,
                    "Event Name": "Climate Forum 2026",
                    "Event URL": "https://example.com/events/climate-forum",
                }
            ],
            self.config,
            session=session,
        )[0]

        self.assertEqual(event["Start Date"].isoformat(), "2026-09-09")
        self.assertEqual(event["Start Time"].isoformat(), "11:30:00")
        self.assertEqual(event["Venue Name"], "City Conference Centre")
        self.assertIn("finance resilient infrastructure", event["Summary"])
        self.assertIn("Investor panel", event["Agenda"])
        self.assertIsNone(event["Tickets URL"])
        self.assertEqual(event["Detail Page Format"], "markdown_reader")
        self.assertEqual(len(session.urls), 2)

    def test_marks_listing_url_when_no_separate_detail_page_exists(self):
        event = {
            **self.listing_event,
            "Event URL": self.config.url,
        }
        session = FakeSession([])

        result = enrich_event_details(
            [event],
            self.config,
            session=session,
        )[0]

        self.assertEqual(result["Detail Scrape Status"], "skipped_no_detail_url")
        self.assertEqual(session.urls, [])


if __name__ == "__main__":
    unittest.main()
