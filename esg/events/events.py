from events.additional_sources import all_additional_source_events
from utils.db_utils import save_additional_events_to_db


def all_events():
    events = all_additional_source_events()
    saved_count = save_additional_events_to_db(events)
    print(f"Additional-source events upserted: {saved_count}")
    return events


if __name__ == "__main__":
    all_events()
