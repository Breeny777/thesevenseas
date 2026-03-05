import json
import requests
import xml.etree.ElementTree as ET

PLEX_URL = "http://192.168.4.5:32400"
PLEX_TOKEN = "N4uQMmC-SrGdyGcsSQEE"

LIDARR_URL = "http://192.168.4.5:32405"
LIDARR_API_KEY = "99063e0d5e534bc58aa8fee7690a8734"

DRY_RUN = True

PLAYLISTS_JSON = "config/spotify-to-plex/playlists.json"
MISSING_TRACKS_FILE = "config/spotify-to-plex/missing_tracks_spotify.txt"


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


def load_missing_spotify_tracks(path=MISSING_TRACKS_FILE):
    missing = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "open.spotify.com/track/" in line:
                    track_id = line.split("/")[-1]
                    missing.append(track_id)
    except FileNotFoundError:
        pass
    return missing


def search_lidarr(term):
    r = requests.get(
        f"{LIDARR_URL}/api/v1/search",
        params={"term": term, "apikey": LIDARR_API_KEY},
    )
    r.raise_for_status()
    return r.json()


def pick_best_artist(results):
    artists = [r for r in results if "artistName" in r and "foreignArtistId" in r]
    if not artists:
        return None
    return artists[0]


def pick_best_album(results):
    albums = [r for r in results if "album" in r and "artist" in r]
    if not albums:
        return None
    return albums[0]["album"], albums[0]["artist"]


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


def add_album_to_lidarr(album, artist):
    payload = {
        "title": album["title"],
        "foreignAlbumId": album["foreignAlbumId"],
        "foreignArtistId": artist["foreignArtistId"],
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


def process_playlist(pl_entry):
    rk = pl_entry["ratingKey"]
    title = pl_entry["title"]
    print(f"\n=== Processing playlist: {title} (Plex ID {rk}) ===")

    items = fetch_playlist_items(rk)
    print(f"Found {len(items)} tracks in Plex playlist")

    missing_spotify_ids = load_missing_spotify_tracks()

    total_spotify_tracks = len(items) + len(missing_spotify_ids)
    print(f"\nPlaylist summary for {title}:")
    print(f"  Total Spotify tracks: {total_spotify_tracks}")
    print(f"  Tracks matched in Plex: {len(items)}")
    print(f"  Missing tracks: {len(missing_spotify_ids)}")

    if missing_spotify_ids:
        print("\nMissing Spotify track IDs:")
        for tid in missing_spotify_ids:
            print("  -", tid)

    # Artist imports for matched tracks
    artist_album_pairs = set()
    for artist, album, track in items:
        artist_album_pairs.add((artist, album))

    print(f"\nUnique artist/album pairs (matched in Plex): {len(artist_album_pairs)}")

    for artist_name, album_name in sorted(artist_album_pairs):
        print(f"\n{artist_name} — {album_name}")
        results = search_lidarr(artist_name)

        best = pick_best_artist(results)
        if not best:
            print("  No Lidarr artist match.")
            continue

        print("  Matched Lidarr artist:", best["artistName"])
        add_artist_to_lidarr(best)

    # Album imports for missing tracks
    print("\nResolving missing albums...")

    seen_albums = set()

    for track_id in missing_spotify_ids:
        print(f"\nTrack ID {track_id}:")
        results = search_lidarr(track_id)

        album_artist = pick_best_album(results)
        if not album_artist:
            print("  No album match found in Lidarr.")
            continue

        album, artist = album_artist
        album_key = album["foreignAlbumId"]

        if album_key in seen_albums:
            print(f"  Already processed album: {album['title']}")
            continue

        seen_albums.add(album_key)

        print(f"  Album: {album['title']} — Artist: {artist['artistName']}")
        add_album_to_lidarr(album, artist)


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
