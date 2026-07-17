import unittest
from unittest.mock import patch

from events import events as event_pipeline
from events.revised_sources import SOURCE_CONFIGS
from utils.db_utils import REVISED_EVENT_SOURCES


class EventDatabaseScopeTests(unittest.TestCase):
    def test_allowlist_contains_exactly_the_71_revised_sources(self):
        self.assertEqual(
            REVISED_EVENT_SOURCES,
            {config.source for config in SOURCE_CONFIGS.values()},
        )
        self.assertEqual(len(REVISED_EVENT_SOURCES), 71)

    def test_all_events_scrapes_and_saves_revised_sources(self):
        revised_events = [
            {
                "Event Name": "Climate Bonds Forum",
                "Event ID": "cbi-1",
                "Source": "Climate Bonds Initiative",
            }
        ]
        with patch(
            "events.events.scrape_all_revised_events",
            return_value=revised_events,
        ) as scrape, patch(
            "events.events.save_revised_events_to_db",
            return_value=1,
        ) as save:
            result = event_pipeline.all_events()

        self.assertEqual(result, revised_events)
        scrape.assert_called_once_with()
        save.assert_called_once_with(revised_events)


if __name__ == "__main__":
    unittest.main()
