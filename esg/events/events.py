from events.revised_sources import scrape_all_revised_events
from utils.db_utils import save_revised_events_to_db


def all_events():
    events = scrape_all_revised_events()
    saved_count = save_revised_events_to_db(events)
    print(f"Revised-source events upserted: {saved_count}")
    return events


if __name__ == "__main__":
    all_events()
