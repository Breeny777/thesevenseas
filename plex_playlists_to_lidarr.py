import json
import requests
import xml.etree.ElementTree as ET

PLEX_URL = "http://192.168.4.5:32400"
PLEX_TOKEN = "N4uQMmC-SrGdyGcsSQEE"

LIDARR_URL = "http://192.168.4.5:32405"
LIDARR_API_KEY = "99063e0d5e534bc58aa8fee7690a8734"

DRY_RUN = True

PLAYLISTS_JSON = "config/spotify-to-plex/playlists.json"


def plex_get(path):
    url = f"{PLEX_URL}{path}"
    if "X-Plex-Token" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}X-Plex-Token={PLEX_TOKEN}"
    r = requests.get(url)
    r.raise_for_status()
    return r.text


def load_spotify_playlists(path):
    with open(path, "r") as f:
        data = json.load(f)
    return data["data"]


def fetch_plex_playlists():
    xml = plex_get("/playlists")
    root = ET.fromstring(xml)
    playlists = {}
    for pl in root.findall("Playlist"):
        rating_key = pl.get("ratingKey")
        title = pl.get("title")
        playlist_type = pl.get("playlistType")
        smart = pl.get("smart")
        if playlist_type == "audio" and smart == "0":
            playlists[rating_key] = title
    return playlists


def fetch_playlist_items(rating_key):
    xml = plex_get(f"/playlists/{rating_key}/items")
    root = ET.fromstring(xml)
    items = []
    for track in root.findall("Track"):
        artist = track.get("grandparentTitle")
        album = track.get("parentTitle")
        title = track.get("title")
        if artist and album:
            items.append((artist, album, title))
    return items


def search_lidarr_artist(name):
    r = requests.get(
        f"{LIDARR_URL}/api/v1/search",
        params={"term": name, "apikey": LIDARR_API_KEY},
    )
    r.raise_for_status()
    return r.json()


# NEW: Safe filtering of Lidarr results
def pick_best_artist(results):
    artists = [r for r in results if "artistName" in r and "foreignArtistId" in r]
    if not artists:
        return None
    return artists[0]


def add_artist_to_lidarr(artist):
    payload = {
        "artistName": artist["artistName"],
        "foreignArtistId": artist["foreignArtistId"],
        "qualityProfileId": 1,
        "metadataProfileId": 1,
        "rootFolderPath": "/media2/Music",
        "monitored": True,
        "addOptions": {
            "monitor": "all",
            "searchForMissingAlbums": True,
        },
    }

    if DRY_RUN:
        print("[DRY RUN] Would add artist to Lidarr:", payload["artistName"])
        return

    r = requests.post(
        f"{LIDARR_URL}/api/v1/artist",
        params={"apikey": LIDARR_API_KEY},
        json=payload,
    )
    r.raise_for_status()
    print("Added artist to Lidarr:", r.json().get("artistName", payload["artistName"]))


def choose_playlists(spotify_playlists, plex_playlists):
    entries = []
    for entry in spotify_playlists:
        rk = entry["plex"]
        title = plex_playlists.get(rk)
        if title:
            entries.append({"ratingKey": rk, "title": title, "spotify_id": entry["id"]})

    if not entries:
        print("No matching Plex playlists found for spotify-to-plex mappings.")
        return []

    print("Available playlists:")
    for idx, pl in enumerate(entries, start=1):
        print(f"{idx}. {pl['title']} (Plex ID {pl['ratingKey']})")

    choice = input(
        "Enter playlist numbers to process (comma-separated), or 'all': "
    ).strip()

    if choice.lower() == "all":
        return entries

    indices = set()
    for part in choice.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            i = int(part)
            if 1 <= i <= len(entries):
                indices.add(i - 1)
        except ValueError:
            pass

    return [entries[i] for i in sorted(indices)]


def process_playlist(pl_entry):
    rk = pl_entry["ratingKey"]
    title = pl_entry["title"]
    print(f"\n=== Processing playlist: {title} (Plex ID {rk}) ===")

    items = fetch_playlist_items(rk)
    print(f"Found {len(items)} tracks in playlist")

    artist_album_pairs = set()
    for artist, album, track in items:
        artist_album_pairs.add((artist, album))

    print(f"Unique artist/album pairs: {len(artist_album_pairs)}")

    for artist_name, album_name in sorted(artist_album_pairs):
        print(f"\n{artist_name} — {album_name}")
        results = search_lidarr_artist(artist_name)

        best = pick_best_artist(results)
        if not best:
            print("  No Lidarr artist match.")
            continue

        print("  Matched Lidarr artist:", best["artistName"])
        add_artist_to_lidarr(best)


def main():
    spotify_playlists = load_spotify_playlists(PLAYLISTS_JSON)
    plex_playlists = fetch_plex_playlists()

    selected = choose_playlists(spotify_playlists, plex_playlists)
    if not selected:
        print("No playlists selected.")
        return

    print(f"\nDRY_RUN = {DRY_RUN}")
    for pl in selected:
        process_playlist(pl)


if __name__ == "__main__":
    main()
