from bs4 import BeautifulSoup
import requests
from urllib.parse import urljoin
from datetime import datetime
import re

def adb_events():
    url = "https://www.adb.org/news/events/calendar"
    headers = {
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'accept-language': 'en-US,en;q=0.9',
            'cache-control': 'max-age=0',
            'cookie': '_gcl_au=1.1.1129095313.1718286979; _fbp=fb.1.1718286979193.50575138970194135; _gid=GA1.2.1202406196.1721634674; _clck=7npdo8%7C2%7Cfno%7C0%7C1625; cookie-agreed-version=1.0.0; cookie-agreed=2; _ga=GA1.2.1260326193.1718286979; _gat_UA-7621515-1=1; _clsk=71h3y1%7C1721634750538%7C5%7C1%7Cu.clarity.ms%2Fcollect; _ga_XZTWST314D=GS1.1.1721634673.2.1.1721634754.0.0.0',
            'priority': 'u=0, i',
            'referer': 'https://www.adb.org/news',
            'sec-ch-ua': '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'document',
            'sec-fetch-mode': 'navigate',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-user': '?1',
            'upgrade-insecure-requests': '1',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
        }
    response = requests.get(url, headers= headers)
    print("Response status code (ADB):", response.status_code)

    soup = BeautifulSoup(response.content, "html.parser")
    upcoming_events = soup.find_all("div", class_="item-list-divider")
    events_list = []
    for event in upcoming_events:
        month_element = event.find("h3")
        month = month_element.text.strip() if month_element else None
        event_items = event.find_all("li")
        for item in event_items:
            title_element = item.find("span", class_="event-title").find("a")
            title = title_element.text.strip() if title_element else None
            link = title_element['href'] if title_element and 'href' in title_element.attrs else None
            full_link = urljoin(url, link)
            source = "ADB"
            date_element = item.find("span", class_="event-date")
            date = date_element.text.strip() if date_element else None
            location_element = item.find("span", class_="event-location")
            location = location_element.text.strip() if location_element else None
            details_element = item.find("div", class_="collapse")
            details = details_element.text.strip() if details_element else None
            # Parse date string to extract start date
            start_date = None
            end_date = None
            if date:
                # Handle date ranges like "22 - 23 May 2025"
                date_range_match = re.match(r'(\d+)\s*-\s*(\d+)\s+([A-Za-z]+)\s+(\d{4})', date)
                if date_range_match:
                    day_start = date_range_match.group(1)
                    day_end = date_range_match.group(2)
                    month_name = date_range_match.group(3)
                    year = date_range_match.group(4)
                    
                    # Create proper date strings
                    start_date_str = f"{day_start} {month_name} {year}"
                    end_date_str = f"{day_end} {month_name} {year}"
                    
                    try:
                        start_date = datetime.strptime(start_date_str, "%d %B %Y").date()
                        end_date = datetime.strptime(end_date_str, "%d %B %Y").date()
                    except ValueError:
                        try:
                            # Try with abbreviated month name
                            start_date = datetime.strptime(start_date_str, "%d %b %Y").date()
                            end_date = datetime.strptime(end_date_str, "%d %b %Y").date()
                        except ValueError:
                            # Keep as string if parsing fails
                            start_date = date
                            end_date = None
                else:
                    # Try to parse single date formats
                    try:
                        start_date = datetime.strptime(date, "%d %B %Y").date()
                    except ValueError:
                        try:
                            start_date = datetime.strptime(date, "%d %b %Y").date()
                        except ValueError:
                            # Keep as string if parsing fails
                            start_date = date
            
            events_list.append({
                    "Event Name": title,
                    "Event URL": full_link,
                    "Start Date": start_date,
                    "End Date": end_date,
                    "Venue Address": location,
                    "Summary": details,
                    "Source": source,
                    "Month": month
                })
    print("Total upcoming events (ADB):", len(events_list))
    return events_list

def main():
    events = adb_events()
    print(f"ADB events scraped without database writes: {len(events)}")

if __name__ == "__main__":
    main()
