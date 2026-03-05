import csv
import json
import requests
import xml.etree.ElementTree as ET
import time
from pathlib import Path

PLEX_URL = "http://192.168.4.5:32400"
PLEX_TOKEN = "N4uQMmC-SrGdyGcsSQEE"

LIDARR_URL = "http://192.168.4.5:32405"
LIDARR_API_KEY = "99063e0d5e534bc58aa8fee7690a8734"

DRY_RUN = True

PLAYLISTS_JSON = "config/spotify-to-plex/playlists.json"
SCRIPT_DIR = Path(__file__).resolve().parent
CSV_PATH = SCRIPT_DIR / "playlist_export.csv"  # Exportify CSV for this playlist


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
        if artist and album and title:
            items.append((artist, album, title))
    return items


def load_exportify_csv(path):
    """
    CSV header:
    "Track URI","Track Name","Artist URI(s)","Artist Name(s)",
    "Album URI","Album Name","Album Artist URI(s)","Album Artist Name(s)",
    "Album Release Date","Album Image URL","Disc Number","Track Number",
    "Track Duration (ms)","Track Preview URL","Explicit","Popularity",
    "ISRC","Added By","Added At"
    """
    tracks = []
    p = Path(path)
    if not p.exists():
        print(f"Exportify CSV not found at {path}")
        return tracks

    with p.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            track_name = row.get("Track Name", "").strip()
            artist_names = row.get("Artist Name(s)", "").strip()
            album_name = row.get("Album Name", "").strip()
            album_artist_names = row.get("Album Artist Name(s)", "").strip()

            # Prefer album artist if present, else fall back to track artist
            artist_for_album = album_artist_names or artist_names

            if not track_name or not album_name or not artist_for_album:
                continue

            tracks.append(
                {
                    "track": track_name,
                    "album": album_name,
                    "artist": artist_for_album,
                }
            )
    return tracks


# ---------------------------------------------------------
# BALANCED LIDARR SEARCH (0.25s delay + 2 retries)
# ---------------------------------------------------------
def search_lidarr(term, retries=2, delay=0.25):
    for attempt in range(retries + 1):
        try:
            time.sleep(delay)
            r = requests.get(
                f"{LIDARR_URL}/api/v1/search",
                params={"term": term, "apikey": LIDARR_API_KEY},
                timeout=10,
            )

            if r.status_code == 503:
                print(f"  Lidarr overloaded (503). Attempt {attempt+1}/{retries+1}.")
                if attempt < retries:
                    time.sleep(1 + attempt)
                    continue
                return []

            r.raise_for_status()
            return r.json()

        except requests.exceptions.RequestException as e:
            print(f"  Lidarr search error: {e}. Attempt {attempt+1}/{retries+1}.")
            if attempt < retries:
                time.sleep(1 + attempt)
                continue
            return []

    return []

def find_album_in_lidarr(album_name, artist_name):
    # 1. Try album-only search
    results = search_lidarr(album_name)
    candidates = []

    for r in results:
        if "album" in r and "artist" in r:
            lidarr_album = r["album"]["title"].strip().lower()
            if lidarr_album.startswith(album_name.lower()):
                candidates.append((r["album"], r["artist"]))

    if candidates:
        return candidates[0]

    # 2. Try artist-only search
    results = search_lidarr(artist_name)

    for r in results:
        if "album" in r and "artist" in r:
            lidarr_album = r["album"]["title"].strip().lower()
            if lidarr_album.startswith(album_name.lower()):
                return (r["album"], r["artist"])

    return None


def add_album_to_lidarr(album, artist):
    payload = {
        "title": album["title"],
        "foreignAlbumId": album.get("foreignAlbumId"),
        "foreignArtistId": artist.get("foreignArtistId"),
        "qualityProfileId": 1,
        "metadataProfileId": 1,
        "monitored": True,
        "rootFolderPath": "/media2/Music",
        "addOptions": {
            "monitor": "missing",
            "searchForMissingAlbums": True,
        },
    }

    if DRY_RUN:
        print(f"[DRY RUN] Would add album to Lidarr: {album['title']} (Artist: {artist['artistName']})")
        return

    r = requests.post(
        f"{LIDARR_URL}/api/v1/album",
        params={"apikey": LIDARR_API_KEY},
        json=payload,
    )
    r.raise_for_status()
    print("Added album to Lidarr:", r.json().get("title", album["title"]))


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

    choice = input("Enter playlist numbers to process (comma-separated), or 'all': ").strip()

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


def normalize_key(artist, album, track=None):
    a = (artist or "").strip().lower()
    b = (album or "").strip().lower()
    if track is None:
        return (a, b)
    t = (track or "").strip().lower()
    return (a, b, t)


def process_playlist(pl_entry):
    rk = pl_entry["ratingKey"]
    title = pl_entry["title"]
    print(f"\n=== Processing playlist: {title} (Plex ID {rk}) ===")

    # Plex side
    plex_items = fetch_playlist_items(rk)
    print(f"Found {len(plex_items)} tracks in Plex playlist")

    plex_track_keys = {normalize_key(a, al, t) for a, al, t in plex_items}

    # CSV side
    csv_tracks = load_exportify_csv(CSV_PATH)
    print(f"Loaded {len(csv_tracks)} tracks from Exportify CSV")

    csv_track_keys = {
        normalize_key(t["artist"], t["album"], t["track"]) for t in csv_tracks
    }

    # Compute missing tracks (in CSV but not in Plex)
    missing_track_keys = csv_track_keys - plex_track_keys

    # Map back to structured tracks
    missing_tracks = []
    for t in csv_tracks:
        key = normalize_key(t["artist"], t["album"], t["track"])
        if key in missing_track_keys:
            missing_tracks.append(t)

    total_spotify_tracks = len(csv_tracks)
    matched_tracks = total_spotify_tracks - len(missing_tracks)

    print(f"\nPlaylist summary for {title}:")
    print(f"  Total Spotify tracks (from CSV): {total_spotify_tracks}")
    print(f"  Tracks matched in Plex: {matched_tracks}")
    print(f"  Missing tracks: {len(missing_tracks)}")

    if missing_tracks:
        print("\nSample missing tracks (up to 10):")
        for t in missing_tracks[:10]:
            print(f"  - {t['artist']} — {t['album']} — {t['track']}")

    # Build unique missing albums
    missing_album_keys = set()
    missing_albums = []

    for t in missing_tracks:
        key = normalize_key(t["artist"], t["album"], None)
        if key in missing_album_keys:
            continue
        missing_album_keys.add(key)
        missing_albums.append(
            {
                "artist": t["artist"],
                "album": t["album"],
            }
        )

    print(f"\nUnique missing albums: {len(missing_albums)}")

    # Resolve missing albums in Lidarr by name
    seen_album_titles = set()

    for entry in missing_albums:
        artist_name = entry["artist"]
        album_name = entry["album"]

        print(f"\nResolving album: {artist_name} — {album_name}")

        term = f"{album_name} {artist_name}"
        results = search_lidarr(term)

        album_artist = find_album_in_lidarr(album_name, artist_name)
        if not album_artist:
            print("  No album match found in Lidarr.")
            continue

        album, artist = album_artist

        # Deduplicate by album title + artist name (string-level)
        dedupe_key = (album["title"].strip().lower(), artist["artistName"].strip().lower())
        if dedupe_key in seen_album_titles:
            print(f"  Already processed album: {album['title']} — {artist['artistName']}")
            continue

        seen_album_titles.add(dedupe_key)

        print(f"  Matched album: {album['title']} — Artist: {artist['artistName']}")
        add_album_to_lidarr(album, artist)


def main():
    spotify_playlists = load_spotify_playlists(PLAYLISTS_JSON)
    plex_playlists = fetch_plex_playlists()

    selected = choose_playlists(spotify_playlists, plex_playlists)
    if not selected:
        print("No playlists selected.")
        return

    print(f"\nDRY_RUN = {DRY_RUN}")
    print(f"Using Exportify CSV: {CSV_PATH}")
    for pl in selected:
        process_playlist(pl)


if __name__ == "__main__":
    main()
