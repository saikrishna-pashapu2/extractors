import copy
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv

try:
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0
except ImportError:  # pragma: no cover - exercised only before dependencies install
    LangDetectException = Exception
    detect = None


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DEFAULT_TRANSLATION_MODEL = "gpt-4o-mini"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
MAX_BATCH_CHARACTERS = 24000
MAX_UNIT_CHARACTERS = 12000

TRANSLATABLE_FIELDS = (
    "Event Name",
    "Detail Page Title",
    "Summary",
    "Agenda",
    "Detail Text",
    "Venue Name",
    "Venue Address",
    "Organizer Name",
    "Ticket Price",
    "Tags",
    "Topics",
    "Speakers",
    "Sponsors",
    "Additional Details",
)

TRANSLATION_DEFAULTS = {
    "Original Language": None,
    "Translation Status": None,
    "Translation Model": None,
    "Translation Error": None,
    "Original Text": None,
}

SYSTEM_PROMPT = """You translate public event information into clear English.
Translate every natural-language string in the supplied JSON array.
Keep each id exactly unchanged and return exactly one translation for every id.
Preserve all facts, dates, times, currencies, URLs, email addresses, phone numbers,
formatting meaning, and organization/person names. Transliterate proper names when
needed for an English reader. Do not summarize, omit, explain, or add information.
If a string is already English or is language-neutral, return it unchanged."""


def translate_events_to_english(
    events: Sequence[Dict[str, Any]],
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    session: Optional[requests.Session] = None,
    continue_on_error: bool = True,
) -> List[Dict[str, Any]]:
    """Translate non-English event fields while preserving their original text."""
    resolved_api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")
    resolved_model = model or os.getenv(
        "OPENAI_TRANSLATION_MODEL",
        DEFAULT_TRANSLATION_MODEL,
    )
    client = session or requests.Session()
    close_client = session is None
    translated_events = []

    try:
        for event in events:
            normalized = _with_translation_defaults(event)
            language = detect_event_language(normalized)
            normalized["Original Language"] = language

            units, skeletons = _collect_event_translation_units(normalized)
            if not units:
                normalized["Translation Status"] = "skipped_no_text"
                translated_events.append(normalized)
                continue
            if language == "en":
                normalized["Translation Status"] = "not_needed"
                translated_events.append(normalized)
                continue
            if not resolved_api_key:
                normalized["Translation Status"] = "skipped_no_api_key"
                normalized["Translation Error"] = "OPENAI_API_KEY is not configured."
                translated_events.append(normalized)
                continue

            try:
                translations = _request_translations(
                    units,
                    api_key=resolved_api_key,
                    model=resolved_model,
                    session=client,
                )
                translated = _apply_translations(normalized, skeletons, translations)
                translated["Translation Model"] = resolved_model
                translated["Translation Error"] = None
                translated_events.append(translated)
            except Exception as exc:
                if not continue_on_error:
                    raise
                normalized["Translation Status"] = "failed"
                normalized["Translation Model"] = resolved_model
                normalized["Translation Error"] = str(exc)
                translated_events.append(normalized)
    finally:
        if close_client:
            client.close()

    return translated_events


def detect_event_language(event: Dict[str, Any]) -> str:
    declared_language = str(event.get("Language") or "").strip().lower()
    if declared_language in {"en", "eng", "english"} or declared_language.startswith(
        "english"
    ):
        return "en"

    sample_parts = []
    for field in ("Event Name", "Summary", "Agenda", "Detail Text"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            sample_parts.append(value[:5000])
    sample = " ".join(sample_parts).strip()
    if not sample or not any(character.isalpha() for character in sample):
        return "unknown"

    words = re.findall(r"[a-z]+", sample.lower())
    english_markers = {
        "and",
        "the",
        "for",
        "with",
        "from",
        "this",
        "that",
        "event",
        "conference",
        "meeting",
        "forum",
        "workshop",
        "summit",
        "webinar",
        "finance",
        "climate",
        "infrastructure",
        "will",
        "are",
        "of",
        "to",
        "in",
    }
    marker_count = len(set(words) & english_markers)
    if len(words) < 8 and marker_count >= 1 and sample.isascii():
        return "en"

    script_language = _language_from_script(sample)
    if script_language:
        return script_language

    if detect is not None:
        try:
            return detect(sample)
        except LangDetectException:
            pass

    if len(words) >= 8 and marker_count >= 2:
        return "en"
    return "unknown"


def _language_from_script(value: str) -> Optional[str]:
    script_patterns = (
        ("ja", r"[\u3040-\u30ff]"),
        ("ko", r"[\uac00-\ud7af]"),
        ("ar", r"[\u0600-\u06ff]"),
        ("ru", r"[\u0400-\u04ff]"),
        ("zh", r"[\u3400-\u4dbf\u4e00-\u9fff]"),
        ("he", r"[\u0590-\u05ff]"),
        ("hi", r"[\u0900-\u097f]"),
    )
    for language, pattern in script_patterns:
        if re.search(pattern, value):
            return language
    return None


def _with_translation_defaults(event: Dict[str, Any]) -> Dict[str, Any]:
    normalized = copy.deepcopy(event)
    for key, default in TRANSLATION_DEFAULTS.items():
        normalized.setdefault(key, default)
    return normalized


def _collect_event_translation_units(
    event: Dict[str, Any],
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    units: List[Dict[str, str]] = []
    skeletons = {}
    for field in TRANSLATABLE_FIELDS:
        value = event.get(field)
        if value in (None, "", [], {}):
            continue
        skeletons[field] = _value_to_skeleton(
            value,
            units,
            translate_keys=field == "Additional Details",
        )
    return units, skeletons


def _value_to_skeleton(
    value: Any,
    units: List[Dict[str, str]],
    translate_keys: bool,
) -> Any:
    if isinstance(value, str):
        if not _has_natural_language(value):
            return ("literal", value)
        unit_ids = []
        for chunk in _split_text(value):
            unit_id = f"t{len(units)}"
            units.append({"id": unit_id, "text": chunk})
            unit_ids.append(unit_id)
        return ("units", unit_ids)

    if isinstance(value, list):
        return (
            "list",
            [
                _value_to_skeleton(item, units, translate_keys=translate_keys)
                for item in value
            ],
        )

    if isinstance(value, dict):
        entries = []
        for key, child in value.items():
            key_skeleton = (
                _value_to_skeleton(str(key), units, translate_keys=False)
                if translate_keys
                else ("literal", key)
            )
            entries.append(
                (
                    key_skeleton,
                    _value_to_skeleton(child, units, translate_keys=translate_keys),
                )
            )
        return ("dict", entries)

    return ("literal", value)


def _has_natural_language(value: str) -> bool:
    text = value.strip()
    if not text or re.fullmatch(r"(?:https?://|mailto:|tel:)[^\s]+", text, re.I):
        return False
    if re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", text):
        return False
    return any(character.isalpha() for character in text)


def _split_text(value: str) -> List[str]:
    if len(value) <= MAX_UNIT_CHARACTERS:
        return [value]
    chunks = []
    remaining = value
    while len(remaining) > MAX_UNIT_CHARACTERS:
        split_at = remaining.rfind(" ", 0, MAX_UNIT_CHARACTERS)
        if split_at < MAX_UNIT_CHARACTERS // 2:
            split_at = MAX_UNIT_CHARACTERS
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _request_translations(
    units: Sequence[Dict[str, str]],
    api_key: str,
    model: str,
    session: requests.Session,
) -> Dict[str, str]:
    translations = {}
    for batch in _batch_units(units):
        batch_translations = _request_translation_batch(
            batch,
            api_key=api_key,
            model=model,
            session=session,
        )
        translations.update(batch_translations)
    return translations


def _batch_units(
    units: Sequence[Dict[str, str]],
) -> List[List[Dict[str, str]]]:
    batches = []
    current = []
    current_characters = 0
    for unit in units:
        size = len(unit["text"])
        if current and current_characters + size > MAX_BATCH_CHARACTERS:
            batches.append(current)
            current = []
            current_characters = 0
        current.append(unit)
        current_characters += size
    if current:
        batches.append(current)
    return batches


def _request_translation_batch(
    units: Sequence[Dict[str, str]],
    api_key: str,
    model: str,
    session: requests.Session,
) -> Dict[str, str]:
    total_characters = sum(len(unit["text"]) for unit in units)
    payload = {
        "model": model,
        "instructions": SYSTEM_PROMPT,
        "input": json.dumps({"items": list(units)}, ensure_ascii=False),
        "max_output_tokens": min(16000, max(1000, total_characters // 2 + 500)),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "event_translations",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "translations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "text": {"type": "string"},
                                },
                                "required": ["id", "text"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["translations"],
                    "additionalProperties": False,
                },
            }
        },
        "store": False,
    }
    headers = {
        "authorization": f"Bearer {api_key}",
        "content-type": "application/json",
    }
    timeout = float(os.getenv("OPENAI_TRANSLATION_TIMEOUT", "90"))

    response = None
    for attempt in range(3):
        try:
            response = session.post(
                OPENAI_RESPONSES_URL,
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                break
        except requests.RequestException:
            if attempt == 2:
                raise
        if attempt < 2:
            time.sleep(2**attempt)
    else:
        response.raise_for_status()

    parsed = json.loads(_response_output_text(response.json()))
    returned = {
        item["id"]: item["text"]
        for item in parsed.get("translations", [])
        if isinstance(item, dict) and item.get("id") and isinstance(item.get("text"), str)
    }
    expected_ids = {unit["id"] for unit in units}
    missing_ids = expected_ids - returned.keys()
    if missing_ids:
        raise ValueError(
            f"Translation response omitted {len(missing_ids)} required text values."
        )
    return {unit_id: returned[unit_id] for unit_id in expected_ids}


def _response_output_text(payload: Dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    for output in payload.get("output", []):
        if not isinstance(output, dict):
            continue
        for content in output.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str):
                    return text
    raise ValueError("OpenAI response did not contain output text.")


def _apply_translations(
    event: Dict[str, Any],
    skeletons: Dict[str, Any],
    translations: Dict[str, str],
) -> Dict[str, Any]:
    translated = copy.deepcopy(event)
    original_text = {}
    for field, skeleton in skeletons.items():
        translated_value = _restore_skeleton(skeleton, translations)
        if translated_value != event.get(field):
            original_text[field] = copy.deepcopy(event.get(field))
            translated[field] = translated_value

    translated["Original Text"] = original_text or None
    translated["Translation Status"] = "translated" if original_text else "not_needed"
    return translated


def _restore_skeleton(skeleton: Any, translations: Dict[str, str]) -> Any:
    kind, value = skeleton
    if kind == "literal":
        return value
    if kind == "units":
        return " ".join(translations[unit_id].strip() for unit_id in value).strip()
    if kind == "list":
        return [_restore_skeleton(item, translations) for item in value]
    if kind == "dict":
        return {
            _restore_skeleton(key, translations): _restore_skeleton(child, translations)
            for key, child in value
        }
    raise ValueError(f"Unsupported translation skeleton: {kind}")


__all__ = [
    "DEFAULT_TRANSLATION_MODEL",
    "detect_event_language",
    "translate_events_to_english",
]
