import csv
import unittest
from unittest.mock import ANY, Mock, call, mock_open, patch

from events.event_page_scraper import enrich_event_details
from events.revised_sources import (
    REVISED_EVENT_SOURCES,
    SOURCE_CONFIGS,
    _event_page_config,
    _filter_events,
    _scrape_revised_source_listing,
    scrape_all_revised_events,
    scrape_revised_source,
    write_events_file,
)


class RevisedSourceRegistryTests(unittest.TestCase):
    def test_registry_contains_all_71_workbook_sources(self):
        self.assertEqual(len(REVISED_EVENT_SOURCES), 71)
        self.assertEqual(len(SOURCE_CONFIGS), 71)
        self.assertIn("adb", SOURCE_CONFIGS)
        self.assertIn("wecoop", SOURCE_CONFIGS)
        self.assertEqual(
            SOURCE_CONFIGS["net_zero_tracker"].url,
            "https://zerotracker.net/insights",
        )

    def test_extra_urls_from_comments_are_registered(self):
        emirates_urls = {
            page.url for page in SOURCE_CONFIGS["emirates_gbc"].pages
        }
        self.assertEqual(
            emirates_urls,
            {
                "https://emiratesgbc.org/technical-workshops/",
                "https://emiratesgbc.org/annual-emiratesgbc-congress/",
                "https://emiratesgbc.org/awards/",
                "https://emiratesgbc.org/sponsorship-opportunities/",
            },
        )
        self.assertEqual(
            {page.url for page in SOURCE_CONFIGS["european_commission"].pages},
            {
                "https://european-union.europa.eu/news-and-events/events_en",
                "https://energy.ec.europa.eu/events_en",
                "https://www.eea.europa.eu/en/newsroom/events",
            },
        )
        self.assertEqual(len(SOURCE_CONFIGS["imo"].pages), 2)

    def test_adb_include_filter_uses_workbook_keywords(self):
        events = [
            {
                "Event Name": "Climate Finance and Renewable Energy Forum",
                "Summary": "",
            },
            {
                "Event Name": "Regional Procurement Briefing",
                "Summary": "Supplier onboarding and purchasing procedures",
            },
        ]

        result = _filter_events(events, SOURCE_CONFIGS["adb"].pages[0])

        self.assertEqual(
            [event["Event Name"] for event in result],
            ["Climate Finance and Renewable Energy Forum"],
        )

    def test_aiib_exclusion_filter_follows_workbook_comment(self):
        events = [
            {"Event Name": "Graduate Program Recruitment Fair"},
            {"Event Name": "Infrastructure Investment Forum"},
        ]

        result = _filter_events(events, SOURCE_CONFIGS["aiib"].pages[0])

        self.assertEqual(
            [event["Event Name"] for event in result],
            ["Infrastructure Investment Forum"],
        )
        page_config = _event_page_config(
            SOURCE_CONFIGS["aiib"],
            SOURCE_CONFIGS["aiib"].pages[0],
        )
        self.assertEqual(
            page_config.allow_url_patterns,
            (r"/news-events/events/",),
        )
        self.assertIn(
            r"/events/upcoming-events/index\.html",
            page_config.deny_url_patterns,
        )

    def test_unspecified_filter_comments_use_general_esg_keywords(self):
        events = [
            {"Event Name": "Net Zero Transition in Capital Markets"},
            {"Event Name": "Quarterly Earnings Technology Briefing"},
        ]

        result = _filter_events(events, SOURCE_CONFIGS["lseg"].pages[0])

        self.assertEqual(
            [event["Event Name"] for event in result],
            ["Net Zero Transition in Capital Markets"],
        )

    @patch("events.revised_sources.scrape_event_page")
    def test_each_page_uses_its_own_filter_rule(self, page_scraper):
        page_scraper.side_effect = [
            [
                {
                    "Event Name": "General Budget Conference",
                    "Event URL": "https://european-union.europa.eu/events/budget",
                }
            ],
            [
                {
                    "Event Name": "European Energy Market Workshop",
                    "Event URL": "https://energy.ec.europa.eu/events/market",
                }
            ],
            [
                {
                    "Event Name": "Air Quality Science Meeting",
                    "Event URL": "https://www.eea.europa.eu/events/air-quality",
                }
            ],
        ]

        result = _scrape_revised_source_listing(
            "european_commission",
            session=Mock(),
        )

        self.assertEqual(
            [event["Event Name"] for event in result],
            [
                "European Energy Market Workshop",
                "Air Quality Science Meeting",
            ],
        )
        self.assertEqual(page_scraper.call_count, 3)


class RevisedSourcePipelineTests(unittest.TestCase):
    def setUp(self):
        self.listing_events = [
            {
                "Event Name": "Infrastructure Forum",
                "Event ID": "event-1",
                "Event URL": "https://www.aiib.org/events/infrastructure-forum",
                "Source": "Asian Infrastructure Investment Bank (AIIB)",
            },
            {
                "Event Name": "Climate Finance Workshop",
                "Event ID": "event-2",
                "Event URL": "https://www.aiib.org/events/climate-finance-workshop",
                "Source": "Asian Infrastructure Investment Bank (AIIB)",
            },
        ]

    @patch("events.revised_sources.enrich_event_details")
    @patch("events.revised_sources.translate_events_to_english")
    @patch("events.revised_sources._scrape_revised_source_listing")
    def test_single_source_extracts_details_and_translates_by_default(
        self,
        listing_scraper,
        translator,
        detail_scraper,
    ):
        listing_scraper.return_value = self.listing_events
        detailed = [
            {**event, "Detail Scrape Status": "ok"}
            for event in self.listing_events
        ]
        translated = [
            {**event, "Translation Status": "not_needed"} for event in detailed
        ]
        detail_scraper.return_value = detailed
        translator.return_value = translated
        session = Mock()

        result = scrape_revised_source("aiib", session=session)

        self.assertEqual(result, translated)
        detail_scraper.assert_called_once_with(
            self.listing_events,
            ANY,
            session=session,
            continue_on_error=True,
        )
        detail_config = detail_scraper.call_args.args[1]
        self.assertEqual(detail_config.key, "aiib")
        translator.assert_called_once_with(
            detailed,
            session=session,
            continue_on_error=True,
        )

    @patch("events.revised_sources.enrich_event_details")
    @patch("events.revised_sources.translate_events_to_english")
    @patch("events.revised_sources._scrape_revised_source_listing")
    def test_no_details_is_explicit_opt_out(
        self,
        listing_scraper,
        translator,
        detail_scraper,
    ):
        listing_scraper.return_value = self.listing_events
        translator.return_value = self.listing_events[:1]

        result = scrape_revised_source(
            "aiib",
            session=Mock(),
            include_details=False,
            limit=1,
        )

        self.assertEqual(result, self.listing_events[:1])
        detail_scraper.assert_not_called()
        translator.assert_called_once()

    @patch("events.revised_sources.scrape_revised_source")
    def test_combined_run_enables_details_for_every_source(self, source_scraper):
        source_scraper.side_effect = [
            [{**self.listing_events[0], "Source": "AIIB"}],
            [{**self.listing_events[1], "Source": "CDP"}],
        ]

        result = scrape_all_revised_events(source_keys=["aiib", "cdp"])

        self.assertEqual(len(result), 2)
        self.assertEqual(
            source_scraper.call_args_list,
            [
                call(
                    "aiib",
                    session=ANY,
                    include_details=True,
                    include_translation=True,
                    continue_on_error=True,
                ),
                call(
                    "cdp",
                    session=ANY,
                    include_details=True,
                    include_translation=True,
                    continue_on_error=True,
                ),
            ],
        )

    def test_extra_listing_page_is_not_scraped_as_an_event_detail(self):
        source = SOURCE_CONFIGS["emirates_gbc"]
        config = _event_page_config(
            source,
            source.pages[0],
            include_all_listing_urls=True,
        )
        event = {
            "Event Name": "EmiratesGBC Awards",
            "Event URL": "https://emiratesgbc.org/awards/",
        }
        session = Mock()

        result = enrich_event_details([event], config, session=session)

        self.assertEqual(result[0]["Detail Scrape Status"], "skipped_no_detail_url")
        session.get.assert_not_called()

    def test_csv_output_is_usable_for_source_by_source_review(self):
        event = {
            "Event Name": "Climate Forum",
            "Event URL": "https://example.com/climate-forum",
            "Tags": ["Climate", "Finance"],
        }
        opened_file = mock_open()

        with patch("events.revised_sources.Path.open", opened_file):
            write_events_file([event], "events.csv")

        written = "".join(
            call.args[0] for call in opened_file().write.call_args_list
        )
        rows = list(csv.DictReader(written.splitlines()))
        self.assertEqual(rows[0]["Event Name"], "Climate Forum")
        self.assertEqual(rows[0]["Tags"], '["Climate", "Finance"]')


if __name__ == "__main__":
    unittest.main()
