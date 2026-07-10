import json
import unittest

from events.event_translator import (
    detect_event_language,
    translate_events_to_english,
)


class FakeOpenAIResponse:
    status_code = 200

    def __init__(self, translations):
        self.translations = translations

    def raise_for_status(self):
        return None

    def json(self):
        output = json.dumps(
            {"translations": self.translations},
            ensure_ascii=False,
        )
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": output}],
                }
            ]
        }


class FakeOpenAISession:
    def __init__(self, translations):
        self.translation_values = translations
        self.requests = []

    def post(self, url, **kwargs):
        self.requests.append((url, kwargs))
        items = json.loads(kwargs["json"]["input"])["items"]
        translated = [
            {
                "id": item["id"],
                "text": self.translation_values.get(item["text"], item["text"]),
            }
            for item in items
        ]
        return FakeOpenAIResponse(translated)


class EventTranslatorTests(unittest.TestCase):
    def test_translates_all_event_text_and_preserves_original_fields(self):
        event = {
            "Event Name": "気候金融フォーラム",
            "Summary": "投資家と政策立案者のための会議です。",
            "Agenda": "開会と基調講演",
            "Venue Name": "東京国際フォーラム",
            "Topics": ["気候金融"],
            "Additional Details": {"会場": "東京", "日時": "2026年9月9日"},
            "Language": "Japanese",
            "Event ID": "jp-1",
        }
        translations = {
            "気候金融フォーラム": "Climate Finance Forum",
            "投資家と政策立案者のための会議です。": (
                "A conference for investors and policymakers."
            ),
            "開会と基調講演": "Opening and keynote address",
            "東京国際フォーラム": "Tokyo International Forum",
            "気候金融": "Climate finance",
            "会場": "Venue",
            "東京": "Tokyo",
            "日時": "Date and time",
            "2026年9月9日": "9 September 2026",
        }
        session = FakeOpenAISession(translations)

        result = translate_events_to_english(
            [event],
            api_key="test-api-key",
            model="gpt-4o-mini",
            session=session,
        )[0]

        self.assertEqual(detect_event_language(event), "ja")
        self.assertEqual(result["Event Name"], "Climate Finance Forum")
        self.assertEqual(result["Summary"], "A conference for investors and policymakers.")
        self.assertEqual(result["Agenda"], "Opening and keynote address")
        self.assertEqual(result["Topics"], ["Climate finance"])
        self.assertEqual(
            result["Additional Details"],
            {"Venue": "Tokyo", "Date and time": "9 September 2026"},
        )
        self.assertEqual(result["Translation Status"], "translated")
        self.assertEqual(result["Translation Model"], "gpt-4o-mini")
        self.assertEqual(result["Original Language"], "ja")
        self.assertEqual(result["Original Text"]["Event Name"], "気候金融フォーラム")
        self.assertEqual(len(session.requests), 1)
        request = session.requests[0][1]
        self.assertEqual(request["headers"]["authorization"], "Bearer test-api-key")
        self.assertEqual(
            request["json"]["text"]["format"]["type"],
            "json_schema",
        )

    def test_skips_api_for_english_events(self):
        event = {
            "Event Name": "Climate Finance Forum",
            "Summary": "The event brings investors and policymakers together.",
        }
        session = FakeOpenAISession({})

        result = translate_events_to_english(
            [event],
            api_key="test-api-key",
            session=session,
        )[0]

        self.assertEqual(result["Original Language"], "en")
        self.assertEqual(result["Translation Status"], "not_needed")
        self.assertEqual(session.requests, [])

    def test_keeps_non_english_text_when_api_key_is_missing(self):
        event = {"Event Name": "持続可能な投資会議"}

        result = translate_events_to_english(
            [event],
            api_key="",
            session=FakeOpenAISession({}),
        )[0]

        self.assertEqual(result["Event Name"], "持続可能な投資会議")
        self.assertEqual(result["Translation Status"], "skipped_no_api_key")
        self.assertIn("OPENAI_API_KEY", result["Translation Error"])


if __name__ == "__main__":
    unittest.main()
