from events.revised_sources import scrape_all_revised_events
from utils.db_utils import save_revised_events_to_db


def all_events():
    saved_count = 0

    def save_source_events(source_key, source_events):
        nonlocal saved_count
        source_saved_count = save_revised_events_to_db(source_events)
        saved_count += source_saved_count
        print(
            f"Revised-source events upserted ({source_key}): "
            f"{source_saved_count}"
        )

    events = scrape_all_revised_events(on_source_events=save_source_events)
    print(f"Total revised-source events upserted: {saved_count}")
    return events


if __name__ == "__main__":
    all_events()
