import requests
import urllib.parse

LIDARR_URL = "http://plexhost:32405"
API_KEY = "99063e0d5e534bc58aa8fee7690a8734"
ROOT_FOLDER = "/media2/Music"

# Common separators in Spotify exports
SEPARATORS = [",", "&", "feat.", "Feat.", "FEAT.", "featuring", ";"]

def split_artists(name):
    for sep in SEPARATORS:
        if sep in name:
            parts = [p.strip() for p in name.split(sep)]
            return parts
    return [name]

def add_artist(name):
    print(f"\nSearching for: {name}")

    query = urllib.parse.quote(name)
    lookup_url = f"{LIDARR_URL}/api/v1/artist/lookup?term={query}&apikey={API_KEY}"

    try:
        results = requests.get(lookup_url).json()
    except Exception as e:
        print(f"  ❌ Error contacting Lidarr: {e}")
        return

    # Ensure results is a list with at least one entry
    if not isinstance(results, list) or len(results) == 0:
        print(f"  ❌ No match found for: {name}")
        return

    artist = results[0]
    print(f"  ✔ Found: {artist['artistName']}")

    payload = {
        "artistName": artist["artistName"],
        "foreignArtistId": artist["foreignArtistId"],
        "qualityProfileId": 1,
        "metadataProfileId": 1,
        "rootFolderPath": ROOT_FOLDER,
        "monitored": True,
        "addOptions": {
            "monitor": "none",
            "searchForMissingAlbums": False
        }
    }

    add_url = f"{LIDARR_URL}/api/v1/artist?apikey={API_KEY}"
    response = requests.post(add_url, json=payload)

    if response.status_code == 201:
        print(f"  🎉 Added to Lidarr: {artist['artistName']}")
    elif response.status_code == 400:
        print(f"  ⚠ Already exists in Lidarr: {artist['artistName']}")
    else:
        print(f"  ❌ Error adding {artist['artistName']}: {response.status_code}")

def main():
    with open("artists.txt", "r", encoding="utf-8") as f:
        raw_artists = [line.strip() for line in f if line.strip()]

    print(f"Found {len(raw_artists)} raw entries in artists.txt")

    # Expand multi-artist entries
    expanded = []
    for entry in raw_artists:
        expanded.extend(split_artists(entry))

    # Deduplicate while preserving order
    seen = set()
    artists = []
    for a in expanded:
        if a.lower() not in seen:
            seen.add(a.lower())
            artists.append(a)

    print(f"Processing {len(artists)} unique artists...")

    for artist in artists:
        add_artist(artist)

if __name__ == "__main__":
    main()
