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
            "sustainable_fitch_events": "sustainable_fitch",
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

if __name__ == "__main__":
    unittest.main()
