import unittest
from unittest.mock import Mock, patch

from utils import db_utils


class DatabaseEnvironmentTests(unittest.TestCase):
    def test_connect_uses_environment_derived_configuration(self):
        with patch.multiple(
            db_utils,
            DB_NAME="events",
            DB_USER="event_user",
            DB_PASS="secret",
            DB_HOST="database.example.com",
            DB_PORT="5432",
        ), patch("utils.db_utils.psycopg2.connect") as connect:
            db_utils._connect()

        connect.assert_called_once_with(
            dbname="events",
            user="event_user",
            password="secret",
            host="database.example.com",
            port="5432",
        )

    def test_connect_reports_missing_environment_variables(self):
        with patch.multiple(
            db_utils,
            DB_NAME=None,
            DB_USER=None,
            DB_PASS=None,
            DB_HOST=None,
            DB_PORT="5432",
        ), patch("utils.db_utils.psycopg2.connect") as connect:
            with self.assertRaisesRegex(RuntimeError, "DB_NAME, DB_USER, DB_PASSWORD, DB_HOST"):
                db_utils._connect()

        connect.assert_not_called()

    def test_event_saver_rejects_non_revised_sources_before_connecting(self):
        event = {
            "Event Name": "ADB Event",
            "Event ID": "adb-1",
            "Source": "ADB",
        }
        with patch("utils.db_utils.create_events_table") as create_table, patch(
            "utils.db_utils._connect"
        ) as connect:
            with self.assertRaisesRegex(ValueError, "non-revised event sources: ADB"):
                db_utils.save_events_to_db([event])

        create_table.assert_not_called()
        connect.assert_not_called()

    def test_revised_event_upsert_preserves_full_json_payload(self):
        event = {
            "Event Name": "Climate Bonds Forum",
            "Event ID": "cbi-1",
            "Event URL": "https://example.com/climate-bonds-forum",
            "Tags": ["Reporting", "ESG"],
            "Source": "Climate Bonds Initiative",
            "Detail Scrape Status": "ok",
            "Original Language": "fr",
            "Translation Status": "translated",
            "Translation Model": "gpt-4o-mini",
            "Agenda": "Opening session",
            "Additional Details": {"Audience": "Reporters"},
        }
        connection = Mock()
        cursor = Mock()
        connection.cursor.return_value = cursor
        with patch("utils.db_utils.create_events_table"), patch(
            "utils.db_utils._connect",
            return_value=connection,
        ):
            saved = db_utils.save_revised_events_to_db([event])

        self.assertEqual(saved, 1)
        sql, values = cursor.execute.call_args.args
        self.assertIn("ON CONFLICT (event_id) DO UPDATE", sql)
        self.assertEqual(len(values), 24)
        self.assertEqual(values[16], '["Reporting", "ESG"]')
        self.assertIsInstance(values[19], db_utils.Json)
        self.assertEqual(values[20:24], ("ok", "fr", "translated", "gpt-4o-mini"))
        connection.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
