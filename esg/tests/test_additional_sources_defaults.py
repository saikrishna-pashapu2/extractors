import unittest
from unittest.mock import Mock, patch

from events import additional_sources


class AdditionalSourceDefaultTests(unittest.TestCase):
    def setUp(self):
        self.event = {
            "Event Name": "ESG Reporting Forum",
            "Event ID": "gri-1",
            "Event URL": "https://example.com/events/esg-reporting-forum",
            "Source": "Global Reporting Initiative (GRI)",
        }

    def test_direct_source_call_enriches_details_then_translates(self):
        session = Mock()
        listing_scraper = Mock(return_value=[self.event])
        detailed = [{**self.event, "Detail Scrape Status": "ok"}]
        translated = [
            {
                **detailed[0],
                "Translation Status": "not_needed",
            }
        ]

        with patch.dict(
            additional_sources._ADDITIONAL_LISTING_SCRAPERS,
            {"gri": listing_scraper},
        ), patch(
            "events.additional_sources.enrich_event_details",
            return_value=detailed,
        ) as detail_scraper, patch(
            "events.additional_sources.translate_events_to_english",
            return_value=translated,
        ) as translator:
            result = additional_sources.gri_events(session=session)

        self.assertEqual(result, translated)
        listing_scraper.assert_called_once_with(session=session)
        detail_scraper.assert_called_once_with(
            [self.event],
            additional_sources.ADDITIONAL_SOURCE_CONFIGS["gri"],
            session=session,
            continue_on_error=True,
        )
        translator.assert_called_once_with(
            detailed,
            session=session,
            continue_on_error=True,
        )

    def test_every_public_additional_source_uses_shared_pipeline(self):
        wrappers = {
            "gri_events": "gri",
            "gggi_events": "gggi",
            "sustainable_fitch_events": "sustainable_fitch",
            "fitch_ratings_events": "fitch_ratings",
            "sp_global_events": "sp_global",
            "cbuae_events": "cbuae",
            "climate_bonds_events": "climate_bonds_initiative",
            "oecd_events": "oecd",
            "wef_events": "wef",
        }
        session = Mock()
        with patch(
            "events.additional_sources.scrape_additional_source",
            return_value=[],
        ) as shared_scraper:
            for function_name, source_key in wrappers.items():
                with self.subTest(source=source_key):
                    getattr(additional_sources, function_name)(session=session)
                    shared_scraper.assert_called_with(
                        source_key,
                        session=session,
                        include_details=True,
                        include_translation=True,
                        continue_on_error=True,
                    )

    def test_fitch_ratings_feed_maps_upcoming_event_metadata(self):
        response = Mock(status_code=200)
        response.json.return_value = {
            "result": {
                "data": {
                    "allContentfulEvent": {
                        "nodes": [
                            {
                                "eventId": "fitch-2099",
                                "title": "Fitch Climate Risk Forum",
                                "startDate": "2099-09-01T08:30:00",
                                "endDate": "2099-09-01T13:30:00",
                                "isoStartTime": "2099-09-01T08:30+04:00",
                                "isoEndTime": "2099-09-01T13:30+04:00",
                                "timeZone": "Asia/Dubai",
                                "timeZoneAbbreviation": "GST",
                                "vanityUrl": (
                                    "events.fitchratings.com/climate-risk-forum"
                                ),
                                "relativeVanityUrl": "/events/climate-risk-forum",
                                "eventType": "Hosted",
                                "locationName": "Conference Centre",
                                "locationAddress": "1 Green Street",
                                "locationCity": "Dubai",
                                "locationCountry": "United Arab Emirates",
                                "countries": [{"title": "United Arab Emirates"}],
                                "regions": [{"title": "Middle East"}],
                                "sectors": [{"title": "Sovereigns"}],
                                "topics": [{"slug": "topics/esg"}],
                                "languages": [{"slug": "en"}],
                                "image": {
                                    "title": "Climate event",
                                    "gatsbyImageData": {
                                        "images": {
                                            "fallback": {
                                                "src": "https://images.example/event.jpg"
                                            }
                                        }
                                    },
                                },
                            },
                            {
                                "eventId": "fitch-2000",
                                "title": "Past Fitch Event",
                                "startDate": "2000-01-01T08:30:00",
                                "endDate": "2000-01-01T09:30:00",
                            },
                        ]
                    }
                }
            }
        }
        session = Mock()
        session.get.return_value = response

        events = additional_sources._fitch_ratings_listing_events(session=session)

        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["Event ID"], "fitch-2099")
        self.assertEqual(event["Source"], "Fitch Ratings")
        self.assertEqual(
            event["Event URL"],
            "https://events.fitchratings.com/climate-risk-forum",
        )
        self.assertEqual(event["Image URL"], "https://images.example/event.jpg")
        self.assertEqual(event["Countries"], ["United Arab Emirates"])
        self.assertEqual(event["Regions"], ["Middle East"])
        self.assertEqual(event["Sectors"], ["Sovereigns"])
        self.assertEqual(event["Topics"], ["Esg"])
        self.assertEqual(event["Languages"], ["En"])
        self.assertEqual(event["Time Zone Abbreviation"], "GST")
        response.raise_for_status.assert_called_once()

    @patch("events.additional_sources.scrape_event_page")
    def test_gggi_keeps_real_event_urls_and_decodes_content(self, page_scraper):
        page_scraper.return_value = [
            {
                "Event Name": "COP17 &#8211; Desertification",
                "Event ID": "structured-event",
                "Event URL": "https://gggi.org/event/cop17-desertification/",
                "Start Date": "2026-08-17",
                "End Date": "2026-08-28",
                "Venue Name": "Ulaanbaatar",
                "Summary": "&lt;p&gt;Climate action [&hellip;]&lt;/p&gt;\\n",
                "Source": "Global Green Growth Institute (GGGI)",
            },
            {
                "Event Name": "COP17 \ufffd Desertification",
                "Event ID": "html-duplicate",
                "Event URL": "https://gggi.org/event/cop17-desertification/",
                "Start Date": "2026-08-17",
                "End Date": None,
                "Summary": "Climate action",
                "Source": "Global Green Growth Institute (GGGI)",
            },
            {
                "Event Name": "Today",
                "Event ID": "today-control",
                "Event URL": "https://gggi.org/events/list/?posts_per_page=5",
                "Start Date": "2026-08-24",
                "Source": "Global Green Growth Institute (GGGI)",
            },
            {
                "Event Name": "August 2026",
                "Event ID": "month-control",
                "Event URL": "https://gggi.org/events/list/",
                "Start Date": "2026-08-24",
                "Source": "Global Green Growth Institute (GGGI)",
            },
        ]

        events = additional_sources._gggi_listing_events(session=Mock())

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["Event ID"], "structured-event")
        self.assertEqual(events[0]["Event Name"], "COP17 \u2013 Desertification")
        self.assertEqual(events[0]["Summary"], "Climate action [\u2026]")
        self.assertNotIn("&lt;", events[0]["Summary"])

    def test_gggi_normalizes_detail_summary_before_translation(self):
        events = additional_sources.normalize_gggi_events(
            [
                {
                    "Event Name": "COP17 &#8211; Desertification",
                    "Summary": "&lt;p&gt;Climate action [&hellip;]&lt;/p&gt;\\n",
                    "Structured Data": {
                        "description": "&lt;p&gt;Climate action&lt;/p&gt;"
                    },
                }
            ]
        )

        self.assertEqual(events[0]["Event Name"], "COP17 \u2013 Desertification")
        self.assertEqual(events[0]["Summary"], "Climate action [\u2026]")
        self.assertEqual(
            events[0]["Structured Data"]["description"],
            "&lt;p&gt;Climate action&lt;/p&gt;",
        )

if __name__ == "__main__":
    unittest.main()
