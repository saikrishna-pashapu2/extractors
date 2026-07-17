import csv
import json
import logging
import os
from contextlib import closing
from datetime import datetime
from pathlib import Path

import psycopg2
from psycopg2.extras import Json
from dotenv import load_dotenv

from events.revised_sources import SOURCE_CONFIGS as REVISED_SOURCE_CONFIGS


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASS = os.getenv("DB_PASSWORD") or os.getenv("DB_PASS")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT", "5432")

logging.basicConfig(level=logging.DEBUG)

REVISED_EVENT_SOURCES = frozenset(
    config.source for config in REVISED_SOURCE_CONFIGS.values()
)


def _connect():
    config = {
        "dbname": DB_NAME,
        "user": DB_USER,
        "password": DB_PASS,
        "host": DB_HOST,
        "port": DB_PORT,
    }
    missing = [key for key, value in config.items() if not value]
    if missing:
        env_names = {
            "dbname": "DB_NAME",
            "user": "DB_USER",
            "password": "DB_PASSWORD",
            "host": "DB_HOST",
            "port": "DB_PORT",
        }
        missing_names = ", ".join(env_names[key] for key in missing)
        raise RuntimeError(f"Missing required database environment variables: {missing_names}")
    return psycopg2.connect(**config)


def _json_dumps(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda item: item.isoformat() if hasattr(item, "isoformat") else str(item),
    )


def _sanitize_postgres_value(value):
    """Remove NUL characters, which PostgreSQL cannot store in text or JSONB."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {
            _sanitize_postgres_value(key): _sanitize_postgres_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_postgres_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_postgres_value(item) for item in value)
    return value


def _json_text(value):
    if value in (None, ""):
        return None
    return _json_dumps(value) if isinstance(value, (list, dict, tuple)) else str(value)

def article_exists(url):
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''SELECT COUNT(*) FROM esg_articles WHERE link = %s''', (url,))
            return cursor.fetchone()[0] > 0

def save_article(article):
    if article_exists(article['url']):
        logging.debug(f"Duplicate article found, not saving: {article['title']}")
        return
    
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''INSERT INTO esg_articles (title, published, summary, link, source, matched_keywords) 
                              VALUES (%s, %s, %s, %s, %s, %s)''', 
                           (article['title'], article['date'], article['summary'], 
                            article['url'], article['source'], article['keywords']))
            logging.debug(f"Article saved to database: {article['title']}")
            conn.commit()

def fetch_articles_by_date(selected_date):
    try:
        with closing(_connect()) as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute("""
                    SELECT title, published, summary, link, source, matched_keywords 
                    FROM esg_articles
                    WHERE DATE(published) = %s
                    ORDER BY published DESC
                """, (selected_date,))
                articles = cursor.fetchall()
                return articles
    except Exception as e:
        logging.error(f"Error fetching articles from database: {e}")
        return []
    
def pub_exists(url):
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute("SELECT 1 FROM publications WHERE link = %s LIMIT 1", (url,))
            result = cursor.fetchone()
            return result is not None


def save_pub(article):
    if pub_exists(article['link']):
        logging.debug(f"Duplicate article found, not saving: {article['title']}")
        return
    current_date_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''INSERT INTO publications (image_url, title, summary, link, source, published)
                              VALUES (%s, %s, %s, %s, %s, %s)''', 
                           (article['image_url'], article['title'], article['summary'], 
                            article['link'], article['source'], article['date'] or current_date_time))
            logging.debug(f"Article saved to database: {article['title']}")
            conn.commit()



def create_publications_table():
    """Create the publications table if it doesn't exist."""
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS publications (
                    id SERIAL PRIMARY KEY,
                    image_url TEXT,
                    title TEXT,
                    summary TEXT,
                    link TEXT UNIQUE,
                    source TEXT,
                    published TIMESTAMP
                )
            ''')
            conn.commit()
            logging.debug("Table 'publications' created or already exists.")

def create_events_table():
    """Create the events table if it doesn't exist."""
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id SERIAL PRIMARY KEY,
                    event_name TEXT,
                    event_id TEXT UNIQUE,
                    event_url TEXT,
                    start_date DATE,
                    end_date DATE,
                    start_time TIME,
                    end_time TIME,
                    timezone TEXT,
                    image_url TEXT,
                    ticket_price TEXT,
                    tickets_url TEXT,
                    venue_name TEXT,
                    venue_address TEXT,
                    organizer_name TEXT,
                    organizer_url TEXT,
                    summary TEXT,
                    tags TEXT,
                    source TEXT,
                    month TEXT,
                    event_data JSONB,
                    detail_scrape_status TEXT,
                    original_language TEXT,
                    translation_status TEXT,
                    translation_model TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS event_data JSONB")
            cursor.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS detail_scrape_status TEXT"
            )
            cursor.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS original_language TEXT"
            )
            cursor.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS translation_status TEXT"
            )
            cursor.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS translation_model TEXT"
            )
            cursor.execute(
                "ALTER TABLE events ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP "
                "DEFAULT CURRENT_TIMESTAMP"
            )
            conn.commit()
            logging.debug("Table 'events' created or already exists.")

def event_exists(event):
    """Check if an event already exists in the database.
    This version takes the entire event object and tries multiple identification strategies.
    """
    event_id = event.get('Event ID')
    event_title = event.get('Event Name')
    event_url = event.get('Event URL')
    
    # No way to identify this event
    if not event_id and not event_title and not event_url:
        return False
    
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            # First check if the table exists
            cursor.execute("""
                SELECT EXISTS (
                   SELECT FROM information_schema.tables 
                   WHERE  table_schema = 'public'
                   AND    table_name   = 'events'
                );
            """)
            table_exists = cursor.fetchone()[0]
            
            if not table_exists:
                return False
            
            # Try matching by event_id first (most reliable)
            if event_id:
                cursor.execute("SELECT 1 FROM events WHERE event_id = %s LIMIT 1", (event_id,))
                result = cursor.fetchone()
                if result is not None:
                    return True
            
            # If no match by ID and we have a title, try matching by title
            if event_title:
                cursor.execute("SELECT 1 FROM events WHERE event_name = %s LIMIT 1", (event_title,))
                result = cursor.fetchone()
                if result is not None:
                    return True
            
            # If no match by ID or title, try URL if available
            if event_url:
                cursor.execute("SELECT 1 FROM events WHERE event_url = %s LIMIT 1", (event_url,))
                result = cursor.fetchone()
                if result is not None:
                    return True
                    
            return False

def save_events_to_db(
    events,
    also_save_csv=False,
    filename="events.csv",
    allowed_sources=REVISED_EVENT_SOURCES,
):
    """Upsert event records and retain the complete enriched payload as JSONB."""
    events = list(events)
    if not events:
        return 0
    if allowed_sources is not None:
        invalid_sources = sorted(
            {
                str(event.get("Source") or "<missing>")
                for event in events
                if event.get("Source") not in allowed_sources
            }
        )
        if invalid_sources:
            raise ValueError(
                "Refusing to save non-revised event sources: "
                + ", ".join(invalid_sources)
            )
    create_events_table()

    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            saved_event_names = []
            for raw_event in events:
                event = _sanitize_postgres_value(raw_event)
                cursor.execute('''
                    INSERT INTO events (
                        event_name, event_id, event_url, start_date, end_date,
                        start_time, end_time, timezone, image_url, ticket_price,
                        tickets_url, venue_name, venue_address, organizer_name,
                        organizer_url, summary, tags, source, month, event_data,
                        detail_scrape_status, original_language,
                        translation_status, translation_model
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (event_id) DO UPDATE SET
                        event_name = EXCLUDED.event_name,
                        event_url = EXCLUDED.event_url,
                        start_date = EXCLUDED.start_date,
                        end_date = EXCLUDED.end_date,
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        timezone = EXCLUDED.timezone,
                        image_url = EXCLUDED.image_url,
                        ticket_price = EXCLUDED.ticket_price,
                        tickets_url = EXCLUDED.tickets_url,
                        venue_name = EXCLUDED.venue_name,
                        venue_address = EXCLUDED.venue_address,
                        organizer_name = EXCLUDED.organizer_name,
                        organizer_url = EXCLUDED.organizer_url,
                        summary = EXCLUDED.summary,
                        tags = EXCLUDED.tags,
                        source = EXCLUDED.source,
                        month = EXCLUDED.month,
                        event_data = EXCLUDED.event_data,
                        detail_scrape_status = EXCLUDED.detail_scrape_status,
                        original_language = EXCLUDED.original_language,
                        translation_status = EXCLUDED.translation_status,
                        translation_model = EXCLUDED.translation_model,
                        updated_at = CURRENT_TIMESTAMP
                ''', (
                    event.get('Event Name'), event.get('Event ID'), event.get('Event URL'),
                    event.get('Start Date'), event.get('End Date'), event.get('Start Time'),
                    event.get('End Time'), event.get('Timezone'), event.get('Image URL'),
                    event.get('Ticket Price'), event.get('Tickets URL'), event.get('Venue Name'),
                    event.get('Venue Address'), event.get('Organizer Name'), event.get('Organizer URL'),
                    event.get('Summary'), _json_text(event.get('Tags')), event.get('Source'),
                    event.get('Month'), Json(event, dumps=_json_dumps),
                    event.get('Detail Scrape Status'), event.get('Original Language'),
                    event.get('Translation Status'), event.get('Translation Model')
                ))
                saved_event_names.append(event.get("Event Name"))
            conn.commit()
            for event_name in saved_event_names:
                logging.debug(f"Event upserted in database: {event_name}")

    if also_save_csv:
        fieldnames = [
            "Event Name", "Event ID", "Event URL", "Start Date", "End Date",
            "Start Time", "End Time", "Timezone", "Image URL", "Ticket Price",
            "Tickets URL", "Venue Name", "Venue Address", "Organizer Name",
            "Organizer URL", "Summary", "Tags", "Source", "Month"
        ]

        with open(filename, mode='w', newline='', encoding='utf-8') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()

            for event in events:
                writer.writerow(event)
    return len(events)


def save_revised_events_to_db(events):
    """Persist only allowlisted sources from the revised event workbook."""
    events = list(events)
    invalid_sources = sorted(
        {
            str(event.get("Source") or "<missing>")
            for event in events
            if event.get("Source") not in REVISED_EVENT_SOURCES
        }
    )
    if invalid_sources:
        raise ValueError(
            "Refusing to save non-revised event sources: "
            + ", ".join(invalid_sources)
        )
    missing_ids = [event.get("Event Name") for event in events if not event.get("Event ID")]
    if missing_ids:
        raise ValueError(
            f"Refusing to save {len(missing_ids)} revised events without Event ID."
        )
    return save_events_to_db(events)

# For backward compatibility
def save_events_to_csv(events, filename="events.csv"):
    """Legacy function that now calls save_events_to_db with CSV saving enabled."""
    save_events_to_db(events, also_save_csv=True, filename=filename)

def recreate_events_table():
    """Drop and recreate the events table with the correct structure."""
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            # Drop the table if it exists
            cursor.execute("DROP TABLE IF EXISTS events")
            conn.commit()
            
            # Create the table with the correct structure
            cursor.execute('''
                CREATE TABLE events (
                    id SERIAL PRIMARY KEY,
                    event_name TEXT,
                    event_id TEXT UNIQUE,
                    event_url TEXT,
                    start_date DATE,
                    end_date DATE,
                    start_time TIME,
                    end_time TIME,
                    timezone TEXT,
                    image_url TEXT,
                    ticket_price TEXT,
                    tickets_url TEXT,
                    venue_name TEXT,
                    venue_address TEXT,
                    organizer_name TEXT,
                    organizer_url TEXT,
                    summary TEXT,
                    tags TEXT,
                    source TEXT,
                    month TEXT,
                    event_data JSONB,
                    detail_scrape_status TEXT,
                    original_language TEXT,
                    translation_status TEXT,
                    translation_model TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            logging.debug("Table 'events' has been recreated with the correct structure.")

# Update the all_events function in events.py




def create_db():
    with closing(_connect()) as conn:
        with closing(conn.cursor()) as cursor:
            cursor.execute('''CREATE TABLE IF NOT EXISTS esg_articles (
                                id SERIAL PRIMARY KEY,
                                title TEXT,
                                published DATE,
                                summary TEXT,
                                link TEXT UNIQUE,
                                source TEXT,
                                matched_keywords TEXT,
                                save_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                              )''')
            conn.commit()
            logging.debug("Table 'esg_articles' created or already exists.")

def setup_all_tables():
    """Create all tables. Run this once after pointing to a new database."""
    create_db()
    create_publications_table()
    create_events_table()
    logging.info("All tables created successfully.")

if __name__ == "__main__":
    setup_all_tables()
