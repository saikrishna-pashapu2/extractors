from events.additional_sources import sp_global_events


def sp_events():
    """Compatibility entry point for the S&P Global event scraper."""
    return sp_global_events()


def main():
    events = sp_events()
    print(f"Total upcoming events (S&P Global): {len(events)}")


if __name__ == "__main__":
    main()
