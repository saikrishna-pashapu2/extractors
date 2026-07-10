import unittest
from unittest.mock import patch

from events import events as event_pipeline
from utils.db_utils import ADDITIONAL_EVENT_SOURCES


class EventDatabaseScopeTests(unittest.TestCase):
    def test_allowlist_contains_exactly_the_seven_additional_sources(self):
        self.assertEqual(
            ADDITIONAL_EVENT_SOURCES,
            {
                "Global Reporting Initiative (GRI)",
                "Sustainable Fitch",
                "S&P Global",
                "Central Bank of the UAE (CBUAE)",
                "Climate Bonds Initiative",
                "OECD",
                "World Economic Forum",
            },
        )

    def test_all_events_scrapes_and_saves_only_additional_sources(self):
        additional_events = [
            {
                "Event Name": "Climate Bonds Forum",
                "Event ID": "cbi-1",
                "Source": "Climate Bonds Initiative",
            }
        ]
        with patch(
            "events.events.all_additional_source_events",
            return_value=additional_events,
        ) as scrape, patch(
            "events.events.save_additional_events_to_db",
            return_value=1,
        ) as save:
            result = event_pipeline.all_events()

        self.assertEqual(result, additional_events)
        scrape.assert_called_once_with()
        save.assert_called_once_with(additional_events)


if __name__ == "__main__":
    unittest.main()
