import os
import re
import sys
import ssl
import json
import urllib.request
from datetime import datetime, timedelta
import zoneinfo
import xml.etree.ElementTree as ET
from xml.dom import minidom
from html.parser import HTMLParser

# --- CONFIGURATION ---
BASE_URL = "https://roxiestreams.su"
CATEGORIES = ["soccer", "mlb", "nba", "nfl", "nhl", "fighting", "motorsports"]
DEFAULT_EVENT_DURATION_MINUTES = 180  # 3 hours default duration
CHANNEL_LOGO = f"{BASE_URL}/imgs/iconn.png"

# --- HTML PARSER ---
class EventsHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_events_table = False
        self.in_tbody = False
        self.in_tr = False
        self.in_td = False
        self.current_col = 0
        
        self.current_link = None
        self.current_title = []
        self.current_time = []
        self.in_a = False
        self.in_time_td = False
        
        self.events = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        
        if tag == "table" and attrs_dict.get("id") == "eventsTable":
            self.in_events_table = True
        elif tag == "tbody" and self.in_events_table:
            self.in_tbody = True
        elif tag == "tr" and (self.in_tbody or self.in_events_table):
            self.in_tr = True
            self.current_col = 0
            self.current_link = None
            self.current_title = []
            self.current_time = []
            self.in_a = False
            self.in_time_td = False
        elif tag == "td" and self.in_tr:
            self.in_td = True
            self.current_col += 1
            if "event-start-time" in attrs_dict.get("class", ""):
                self.in_time_td = True
        elif tag == "a" and self.in_td and self.current_col == 1:
            self.in_a = True
            self.current_link = attrs_dict.get("href", "")

    def handle_endtag(self, tag):
        if tag == "table":
            self.in_events_table = False
            self.in_tbody = False
        elif tag == "tbody":
            self.in_tbody = False
        elif tag == "tr" and self.in_tr:
            self.in_tr = False
            if self.current_link:
                title = "".join(self.current_title).strip()
                time_str = "".join(self.current_time).strip()
                if title and time_str:
                    self.events.append({
                        "link": self.current_link,
                        "title": title,
                        "time_str": time_str
                    })
        elif tag == "td":
            self.in_td = False
            self.in_time_td = False
        elif tag == "a":
            self.in_a = False

    def handle_data(self, data):
        if self.in_tr:
            if self.in_a:
                self.current_title.append(data)
            elif self.in_time_td:
                self.current_time.append(data)

# --- HELPERS ---
def fetch_url(url):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    }
    
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, context=ctx, timeout=15) as resp:
        return resp.read().decode('utf-8', errors='ignore')

def parse_to_utc(date_text):
    # Example format: 'July 12, 2026 11:00 AM'
    try:
        # Standardize whitespace
        cleaned = " ".join(date_text.split())
        # Parse naive datetime
        dt_naive = datetime.strptime(cleaned, "%B %d, %Y %I:%M %p")
        # Localize to America/Los_Angeles
        la_tz = zoneinfo.ZoneInfo("America/Los_Angeles")
        dt_la = dt_naive.replace(tzinfo=la_tz)
        # Convert to UTC
        dt_utc = dt_la.astimezone(zoneinfo.ZoneInfo("UTC"))
        return dt_utc
    except Exception as e:
        print(f"  [Time] Error parsing date text '{date_text}': {e}")
        return None

def get_channel_info(link, category):
    channel_id = link.lstrip("/")
    
    # Check for category-streams-X pattern
    match = re.match(r"^([a-zA-Z0-9-]+)-streams-(\d+)$", channel_id)
    if match:
        cat_part, num_part = match.groups()
        cat_name = cat_part.upper() if cat_part.lower() in ["mlb", "nba", "nfl", "nhl", "ppv", "ufc", "wec"] else cat_part.capitalize()
        name = f"Roxie {cat_name} {num_part}"
        group = cat_name
    else:
        # Custom stream page e.g. /motogp, /wec
        cat_name = channel_id.upper() if channel_id.lower() in ["mlb", "nba", "nfl", "nhl", "ppv", "ufc", "wec"] else channel_id.capitalize()
        name = f"Roxie {cat_name}"
        group = category.upper() if category.lower() in ["mlb", "nba", "nfl", "nhl", "ppv", "ufc", "wec"] else category.capitalize()
        
    # Standard overrides for nice visual formatting
    overrides = {
        "motogp": "MotoGP",
        "wec": "WEC",
        "nascar": "NASCAR",
        "ppv": "PPV",
        "ufc": "UFC",
        "mlb": "MLB",
        "nba": "NBA",
        "nfl": "NFL",
        "nhl": "NHL",
    }
    
    name_parts = name.split()
    cleaned_parts = [overrides.get(p.lower(), p) for p in name_parts]
    name = " ".join(cleaned_parts)
    
    group_parts = group.split()
    cleaned_group_parts = [overrides.get(p.lower(), p) for p in group_parts]
    group = " ".join(cleaned_group_parts)
    
    return {
        "id": channel_id,
        "name": name,
        "group": group
    }

# --- STREAM RESOLUTION ---
def get_domains():
    url = f"{BASE_URL}/domainsz53.txt"
    try:
        data = fetch_url(url)
        domains = [d.strip() for d in data.strip().split('\n') if d.strip()]
        if domains:
            return domains
    except Exception as e:
        print(f"  [Fallback] Warning: failed to fetch domainsz53.txt: {e}")
    return ["formaturamaxi.com.br"] # Safe fallback resolved from HAR

def resolve_stream_static(path, domains):
    url = f"{BASE_URL}{path}"
    try:
        html = fetch_url(url)
    except Exception as e:
        print(f"  [Fallback] Error fetching stream page {url}: {e}")
        return None
        
    # 1. Search for direct hardcoded M3U8 URLs
    direct_matches = re.findall(r"['\"](https?://[^\'\"]+\.m3u8)['\"]", html)
    if direct_matches:
        return direct_matches[0]
        
    # 2. Search for getRandomStream('path.m3u8', 'subdomain')
    match = re.search(r"getRandomStream\(\s*['\"]([^'\"]+\.m3u8)['\"]\s*,\s*['\"]([^'\"]+)['\"]\s*\)", html)
    if match:
        stream_path, subdomain = match.groups()
        domain = domains[0] if domains else "formaturamaxi.com.br"
        return f"https://{subdomain}.{domain}/{stream_path}"
        
    # 3. Search for getRandomStream('path.m3u8') using global subdomain variable
    match_default = re.search(r"getRandomStream\(\s*['\"]([^'\"]+\.m3u8)['\"]\s*\)", html)
    if match_default:
        stream_path = match_default.group(1)
        sub_var_match = re.search(r"var\s+subdomain\s*=\s*['\"]([^'\"]+)['\"]", html)
        subdomain = sub_var_match.group(1) if sub_var_match else "tedesco"
        domain = domains[0] if domains else "formaturamaxi.com.br"
        return f"https://{subdomain}.{domain}/{stream_path}"

    return None

def resolve_stream_playwright(path):
    # This is standard Playwright network interception
    from playwright.sync_api import sync_playwright
    
    m3u8_url = None
    with sync_playwright() as p:
        # Launch Chromium headless
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        # Intercept network responses
        def on_response(response):
            nonlocal m3u8_url
            url = response.url
            if ".m3u8" in url:
                m3u8_url = url
                
        page.on("response", on_response)
        
        url = f"{BASE_URL}{path}"
        print(f"  [Playwright] Loading page {url}")
        page.goto(url, wait_until="networkidle", timeout=15000)
        # Give Clappr video player a few seconds to boot and load the stream
        page.wait_for_timeout(3000)
        browser.close()
        
    return m3u8_url

def resolve_stream(path, domains):
    # Try Playwright network interception first
    try:
        print(f"Attempting Playwright interception for channel: {path}")
        url = resolve_stream_playwright(path)
        if url:
            print(f"  Success: Intercepted via Playwright: {url}")
            return url
    except ImportError:
        print("  Playwright is not installed. Using static parser fallback.")
    except Exception as e:
        print(f"  Playwright interception failed: {e}. Using static parser fallback.")
        
    # Fallback to static parser
    print(f"Attempting static fallback for channel: {path}")
    url = resolve_stream_static(path, domains)
    if url:
        print(f"  Success: Resolved via static fallback: {url}")
        return url
        
    print(f"  Failed: Could not resolve stream URL for {path}")
    return None

# --- MAIN EXECUTION ---
def main():
    print(f"Starting Roxie IPTV Scraping Pipeline on {datetime.now().isoformat()}")
    
    # 1. Fetch event listings from categories
    all_events_by_channel = {}
    total_parsed_events = 0
    
    for category in CATEGORIES:
        url = f"{BASE_URL}/{category}"
        print(f"Fetching listings for category: {category} ({url})")
        try:
            html = fetch_url(url)
            parser = EventsHTMLParser()
            parser.feed(html)
            
            print(f"  Parsed {len(parser.events)} events in {category}")
            for ev in parser.events:
                link = ev["link"]
                # Group under channel info
                ch_info = get_channel_info(link, category)
                ch_id = ch_info["id"]
                
                if ch_id not in all_events_by_channel:
                    all_events_by_channel[ch_id] = {
                        "info": ch_info,
                        "raw_link": link,
                        "programmes": []
                    }
                    
                utc_start = parse_to_utc(ev["time_str"])
                if utc_start:
                    all_events_by_channel[ch_id]["programmes"].append({
                        "title": ev["title"],
                        "start_dt": utc_start,
                        "desc": f"Live streaming of {ev['title']} on RoxieStreams."
                    })
                    total_parsed_events += 1
        except Exception as e:
            print(f"Error scraping category {category}: {e}")

    # Empty Output Safeguard check
    if total_parsed_events == 0:
        print("SAFEGUARD TRIGGERED: No active sports events parsed from any schedule pages.")
        print("Aborting generation to preserve existing playlist and EPG files.")
        sys.exit(0)
        
    print(f"Successfully parsed {total_parsed_events} upcoming events across {len(all_events_by_channel)} channels.")
    
    # 2. Resolve streams for channels
    domains = get_domains()
    print(f"Fetched domains for fallback resolution: {domains}")
    
    active_channels = {}
    for ch_id, ch_data in all_events_by_channel.items():
        # Resolve the live stream URL
        stream_url = resolve_stream(ch_data["raw_link"], domains)
        if stream_url:
            active_channels[ch_id] = {
                "info": ch_data["info"],
                "stream_url": stream_url,
                "programmes": ch_data["programmes"]
            }
            
    # Empty Output Safeguard check after stream resolution
    if not active_channels:
        print("SAFEGUARD TRIGGERED: Successfully parsed events, but failed to resolve any stream URLs.")
        print("Aborting generation to preserve existing playlist and EPG files.")
        sys.exit(0)
        
    print(f"Resolved streams for {len(active_channels)} channels out of {len(all_events_by_channel)} total discovered.")

    # 3. Process EPG timing (adjust stop times to prevent overlaps)
    xmltv_programmes = []
    
    for ch_id, ch_data in active_channels.items():
        # Sort programmes chronologically
        programmes = sorted(ch_data["programmes"], key=lambda x: x["start_dt"])
        
        for i, prog in enumerate(programmes):
            start_dt = prog["start_dt"]
            
            # Default duration: 3 hours
            stop_dt = start_dt + timedelta(minutes=DEFAULT_EVENT_DURATION_MINUTES)
            
            # Adjust stop time if there's a subsequent program on the same channel
            if i + 1 < len(programmes):
                next_start = programmes[i + 1]["start_dt"]
                if next_start < stop_dt:
                    stop_dt = next_start # Avoid overlapping
                    
            xmltv_programmes.append({
                "channel_id": ch_id,
                "title": prog["title"],
                "desc": prog["desc"],
                "start": start_dt.strftime("%Y%m%d%H%M%S +0000"),
                "stop": stop_dt.strftime("%Y%m%d%H%M%S +0000")
            })

    # 4. Generate M3U Playlist
    playlist_path = "playlist.m3u"
    print(f"Generating IPTV Playlist: {playlist_path}")
    with open(playlist_path, "w", encoding="utf-8") as f:
        f.write("#EXTM3U\n")
        # Sort channels by group and name for clean UX in players
        sorted_channels = sorted(active_channels.values(), key=lambda x: (x["info"]["group"], x["info"]["name"]))
        for ch in sorted_channels:
            info = ch["info"]
            f.write(
                f'#EXTINF:-1 tvg-id="{info["id"]}" tvg-name="{info["name"]}" tvg-logo="{CHANNEL_LOGO}" group-title="{info["group"]}",{info["name"]}\n'
            )
            f.write(f'{ch["stream_url"]}\n')
            
    # 5. Generate XMLTV EPG
    epg_path = "epg.xml"
    print(f"Generating XMLTV EPG: {epg_path}")
    
    root = ET.Element("tv", {"generator-info-name": "Roxie IPTV EPG Generator"})
    
    # Write channels
    for ch_id, ch_data in sorted(active_channels.items()):
        info = ch_data["info"]
        ch_el = ET.SubElement(root, "channel", {"id": ch_id})
        
        dn_el = ET.SubElement(ch_el, "display-name", {"lang": "en"})
        dn_el.text = info["name"]
        
        ET.SubElement(ch_el, "icon", {"src": CHANNEL_LOGO})
        
    # Write programmes
    for prog in xmltv_programmes:
        p_el = ET.SubElement(root, "programme", {
            "start": prog["start"],
            "stop": prog["stop"],
            "channel": prog["channel_id"]
        })
        
        t_el = ET.SubElement(p_el, "title", {"lang": "en"})
        t_el.text = prog["title"]
        
        d_el = ET.SubElement(p_el, "desc", {"lang": "en"})
        d_el.text = prog["desc"]
        
    # Write out pretty printed XML
    xml_str = ET.tostring(root, encoding="utf-8")
    parsed_xml = minidom.parseString(xml_str)
    pretty_xml = parsed_xml.toprettyxml(indent="  ")
    
    # Fix top-level duplicate XML declaration created by toprettyxml & save
    # toprettyxml adds `<?xml version="1.0" ?>` at the beginning
    with open(epg_path, "w", encoding="utf-8") as f:
        # Standard pretty XML
        f.write(pretty_xml)

    print(f"Pipeline completed successfully. Generated files: {playlist_path}, {epg_path}")

if __name__ == "__main__":
    main()
