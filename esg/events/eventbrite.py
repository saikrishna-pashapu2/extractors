import requests
import json

def eventbrite_events():
    url = "https://www.eventbrite.com/api/v3/destination/search/"
    
    payload = {
        "browse_surface": "search",
        "event_search": {
            "places": ["421174961"],
            "online_events_only": False,
            "dates": ["current_future"],
            "sort": "quality",
            "aggs": {
                "organizertagsautocomplete_agg": {
                    "size": 50
                },
                "tags": {},
                "dates": {}
            }
        },
        "expand.destination_event": [
            "primary_venue",
            "image",
            "ticket_availability",
            "saves"
        ]
    }
    
    payload_json = json.dumps(payload)
    
    headers = {
    'accept': '*/*',
    'accept-language': 'en-US,en;q=0.9',
    'content-type': 'application/json',
    'cookie': 'stableId=7b0b7138-ff78-43f1-8c1d-e38bb5a10900; mgrefby=; G=v%3D2%26i%3D76ed1283-5df6-43f8-8fc4-7eb73788e184%26a%3D12da%26s%3D093d47d3147b8cc49c5eca7b3ece4a229e87e8da; eblang=lo%3Den_US%26la%3Den-us; AS=01df48cd-2cf7-4ed6-9584-516ffaa1d2c6; csrftoken=1926ad7c440c11ef8a1ac789ab77b4e1; mgref=eafil; ajs_user_id=null; ajs_group_id=null; ajs_anonymous_id=%22e055fe23-d5fb-4e19-a9d6-9f4d39e4bf7c%22; location={%22current_place_parent%22:%22United%20Arab%20Emirates%22%2C%22place_type%22:%22locality%22%2C%22current_place%22:%22Dubai%22%2C%22latitude%22:25.0731%2C%22country%22:%22United%20Arab%20Emirates%22%2C%22place_id%22:%22421174961%22%2C%22slug%22:%22united-arab-emirates--dby%22%2C%22longitude%22:55.298}; mgaff299085471697=ebdssbdestsearch; mgaff299085471697=ebdssbdestsearch; django_timezone=Asia/Dubai; mgref=eafil; tcm={"purposes":{"SaleOfInfo":"Auto","Functional":"Auto","Analytics":"Auto","Advertising":"Auto"},"timestamp":"2025-01-28T07:20:33.011Z","confirmed":false,"prompted":false,"updated":false}; guest=identifier%3D76ed1283-5df6-43f8-8fc4-7eb73788e184%26a%3D12da%26s%3D69e6a963a305c012207b70fc8e24f574d6a833b7366addabc5151b5b58df9590; ebEventToTrack=; SS=AE3DLHSqdPOIg8PBjF_6NvTl_TeX02koRw; AN=; _dd_s=rum=0&expire=1738050126907; session=identifier%3D56c7c7a789044dcf9a5433e38a8a70e2%26issuedTs%3D1738049228%26originalTs%3D1738048838%26s%3D8d483b77c710fcf61ec4cb9283752aa32d65eaf4cbe6b03c573f8530cbe263dd; SP=AGQgbbmtgbYz3bnJigiTKTnjwElSmZ_WxhHe9mU-PRL_RLMvjqcUY5kdn44EbrW8T3SLfeZ0bcH7fFSXioT-2ZNckbJBoanq1ZpwR0xJBfnaqawuYXal_ZD9YNKPqz4Y9TiykW1kKId3sUbtTPjy7dFNL9gWkt8DxuFhLqi4J0aHBK4Qkaj8IHhA5-_6cfM3nMr_21DRd-C46gORfBKXpsYB8SMtvLynxiq8JpEC8D3KRM2njqXETRM; AN=; AS=01df48cd-2cf7-4ed6-9584-516ffaa1d2c6; G=v%3D2%26i%3D508b0522-a2f2-4a42-ada2-d83c5007379c%26a%3D139c%26s%3D9a86130e5f6fd3b57dacc12284258a75b4a41fd6; SP=AGQgbbnn9fAS3FR28w-9yP0frzxHsOFv8BNwvN8_prmAaSMZM-64-J7dvpoaS35_PKLJNdmKugMcPCdMuUEguf96HFyvMDkqda8OL3kfalogLgBgDhWSAPGDQDOJOPH0bueDcLuwXeZJfdO37E9dHtYfa0ZeQ2z98mP58D8789obv0cCND3PuIFUMklscZ5B965BzyuAG-5hFnVmRgJWIXrIzem0vEL6mZw4LS2Yrp5H2i8Fd9iJqF8; SS=AE3DLHSqdPOIg8PBjF_6NvTl_TeX02koRw; ebEventToTrack=; eblang=lo%3Den_US%26la%3Den-us; guest=identifier%3D508b0522-a2f2-4a42-ada2-d83c5007379c%26a%3D139c%26s%3D6153e23ed8b9760ba9bf29724003a562967bbb7c4563fa152a61ea4ed64d925a; session=identifier%3D56c7c7a789044dcf9a5433e38a8a70e2%26issuedTs%3D1738049326%26originalTs%3D1738048838%26s%3D54eeb27b614a41bca4b77d98af4117be36d8ddd5d7ff0332c1db218a54d27a53; stableId=7b0b7138-ff78-43f1-8c1d-e38bb5a10900',
    'origin': 'https://www.eventbrite.com',
    'priority': 'u=1, i',
    'referer': 'https://www.eventbrite.com/d/united-arab-emirates--dby/events--today/environmental-and-sustainability/?page=1',
    'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Windows"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'x-csrftoken': '1926ad7c440c11ef8a1ac789ab77b4e1',
    'x-requested-with': 'XMLHttpRequest'
    }

    response = requests.post(url, headers=headers, data=payload_json)
    print("Status Code:", response.status_code)
    try:
        data = response.json()
    except json.JSONDecodeError:
        print("Failed to parse JSON. Response was:")
        print(response.text)
        return []
    events_results = data.get("events", {}).get("results", [])
    parsed_events = []
    for event in events_results:
        event_details = {
            "Event Name": event.get("name"),
            "Event ID": event.get("id"),
            "Event URL": event.get("url"),
            "Start Date": event.get("start_date"),
            "End Date": event.get("end_date"),
            "Start Time": event.get("start_time"),
            "End Time": event.get("end_time"),
            "Timezone": event.get("timezone"),
            "Image URL": event.get("image", {}).get("url"),
            "Ticket Price": event.get("ticket_availability", {}).get("minimum_ticket_price", {}).get("display"),
            "Tickets URL": event.get("tickets_url"),
            "Venue Name": event.get("primary_venue", {}).get("name"),
            "Venue Address": event.get("primary_venue", {}).get("address", {}).get("localized_address_display"),
            "Organizer Name": event.get("primary_organizer", {}).get("name"),
            "Organizer URL": event.get("primary_organizer", {}).get("url"),
            "Summary": event.get("summary"),
            "Tags": [tag.get("display_name") for tag in event.get("tags", [])],
            "Source": "Eventbrite",
            "Month": event.get("start_date", "")[:7]
        }
        
        parsed_events.append(event_details)


    return parsed_events


def main():
    events = eventbrite_events()
    print(f"Eventbrite events scraped without database writes: {len(events)}")

if __name__ == "__main__":
    main()
