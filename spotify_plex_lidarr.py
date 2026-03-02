#!/usr/bin/env python3
import os
import json
import time
import urllib.parse
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
import argparse

# ---------- CONFIG ----------

SPOTIFY_CLIENT_ID = "c7b81c4687a44ec7a664437aca3d0b8f"
SPOTIFY_CLIENT_SECRET = "799ff32ed7de4c45ab64a588eda4962b"
SPOTIFY_REDIRECT_URI = "https://localhost"  # doesn't need to exist on server

TOKEN_PATH = Path.home() / ".config" / "spotify_lidarr" / "token.json"

PLEX_URL = "http://192.168.4.5:32400"
PLEX_TOKEN = "N4uQMmC-SrGdyGcsSQEE"
PLEX_SECTION_ID = 6  # your music library section

LIDARR_URL = "http://192.168.4.5:32405"
LIDARR_API_KEY = "99063e0d5e534bc58aa8fee7690a8734"
LIDARR_ROOT_FOLDER = "/media2/Music"

SEPARATORS = [",", "&", "feat.", "Feat.", "FEAT.", "featuring", ";"]

# ---------- SPOTIFY AUTH ----------

def ensure_token_dir():
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

def save_token(data):
    ensure_token_dir()
    with open(TOKEN_PATH, "w") as f:
        json.dump(data, f)

def load_token():
    if TOKEN_PATH.exists():
        with open(TOKEN_PATH, "r") as f:
            return json.load(f)
    return None

def get_new_token_interactive():
    params = {
        "client_id": SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "scope": "playlist-read-private playlist-read-collaborative",
    }
    url = "https://accounts.spotify.com/authorize?" + urllib.parse.urlencode(params)

    print("\n1) Open this URL on ANY device with a browser:")
    print(url)
    print("\n2) Log in and approve access.")
    print("3) After approval, you'll be redirected to a URL like:")
    print("   http://localhost:8888/callback?code=...&state=...")
    print("4) Copy that FULL URL and paste it here.\n")

    redirect_url = input("Paste the full redirect URL: ").strip()
    parsed = urllib.parse.urlparse(redirect_url)
    qs = urllib.parse.parse_qs(parsed.query)
    code = qs.get("code", [None])[0]
    if not code:
        raise RuntimeError("No 'code' parameter found in redirect URL.")

    token_url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": SPOTIFY_REDIRECT_URI,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    }
    r = requests.post(token_url, data=data)
    r.raise_for_status()
    token_data = r.json()
    save_token(token_data)
    return token_data

def refresh_token(refresh_token):
    token_url = "https://accounts.spotify.com/api/token"
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": SPOTIFY_CLIENT_ID,
        "client_secret": SPOTIFY_CLIENT_SECRET,
    }
    r = requests.post(token_url, data=data)
    r.raise_for_status()
    token_data = r.json()
    if "refresh_token" not in token_data:
        token_data["refresh_token"] = refresh_token
    save_token(token_data)
    return token_data

def get_spotify_access_token():
    token_data = load_token()
    if not token_data:
        token_data = get_new_token_interactive()
    else:
        token_data = refresh_token(token_data["refresh_token"])
    return token_data["access_token"]

# ---------- SPOTIFY API ----------

def spotify_get(url, access_token, params=None):
    headers = {"Authorization": f"Bearer {access_token}"}
    r = requests.get(url, headers=headers, params=params)
    r.raise_for_status()
    return r.json()

def get_playlists(access_token):
    playlists = []
    url = "https://api.spotify.com/v1/me/playlists"
    params = {"limit": 50}
    while url:
        data = spotify_get(url, access_token, params=params)
        playlists.extend(data["items"])
        url = data.get("next")
        params = None
    return playlists

def choose_playlists(playlists):
    print("\nAvailable Spotify playlists:\n")
    for i, pl in enumerate(playlists, start=1):
        print(f"{i:3d}. {pl['name']} ({pl['tracks']['total']} tracks)")
    print("\nEnter playlist numbers to sync (comma-separated), or 'all':")
    choice = input("> ").strip()
    if choice.lower() == "all":
        return playlists
    indices = []
    for part in choice.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            idx = int(part)
            if 1 <= idx <= len(playlists):
                indices.append(idx - 1)
        except ValueError:
            pass
    return [playlists[i] for i in indices]

def get_playlist_tracks(access_token, playlist_id):
    tracks = []
    url = f"https://api.spotify.com/v1/playlists/{playlist_id}/tracks"
    params = {"limit": 100}
    while url:
        data = spotify_get(url, access_token, params=params)
        tracks.extend(data["items"])
        url = data.get("next")
        params = None
    return tracks

# ---------- PLEX API ----------

def get_plex_tracks():
    url = f"{PLEX_URL}/library/sections/{PLEX_SECTION_ID}/all"
    params = {"X-Plex-Token": PLEX_TOKEN}
    r = requests.get(url, params=params)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    have = set()
    for elem in root.findall(".//Track"):
        artist = elem.get("grandparentTitle", "").strip()
        album = elem.get("parentTitle", "").strip()
        title = elem.get("title", "").strip()
        if artist and album and title:
            key = (artist.lower(), album.lower(), title.lower())
            have.add(key)
    return have

# ---------- LIDARR API ----------

def split_artists(name):
    for sep in SEPARATORS:
        if sep in name:
            return [p.strip() for p in name.split(sep)]
    return [name]

def lidarr_artist_lookup(name):
    query = urllib.parse.quote(name)
    url = f"{LIDARR_URL}/api/v1/artist/lookup?term={query}&apikey={LIDARR_API_KEY}"
    r = requests.get(url)
    if r.status_code != 200:
        print(f"  ❌ Lidarr artist lookup failed ({r.status_code}) for {name}")
        return None
    results = r.json()
    if not isinstance(results, list) or not results:
        print(f"  ❌ No Lidarr artist match for {name}")
        return None
    return results[0]

def lidarr_add_artist(artist_obj, dry_run=False):
    if dry_run:
        print(f"  [DRY-RUN] Would add artist: {artist_obj['artistName']}")
        return

    payload = {
        "artistName": artist_obj["artistName"],
        "foreignArtistId": artist_obj["foreignArtistId"],
        "qualityProfileId": 1,
        "metadataProfileId": 1,
        "rootFolderPath": LIDARR_ROOT_FOLDER,
        "monitored": True,
        "addOptions": {
            "monitor": "none",
            "searchForMissingAlbums": False
        }
    }
    url = f"{LIDARR_URL}/api/v1/artist?apikey={LIDARR_API_KEY}"
    r = requests.post(url, json=payload)
    if r.status_code == 201:
        print(f"  🎉 Added artist to Lidarr: {artist_obj['artistName']}")
    elif r.status_code == 400:
        print(f"  ⚠ Artist already exists in Lidarr: {artist_obj['artistName']}")
    else:
        print(f"  ❌ Error adding artist ({r.status_code}): {r.text}")

def lidarr_album_lookup(artist, album):
    query = urllib.parse.quote(f"{artist} {album}")
    url = f"{LIDARR_URL}/api/v1/album/lookup?term={query}&apikey={LIDARR_API_KEY}"
    r = requests.get(url)
    if r.status_code != 200:
        print(f"  ❌ Lidarr album lookup failed ({r.status_code}) for {artist} – {album}")
        return None
    results = r.json()
    if not isinstance(results, list) or not results:
        print(f"  ❌ No Lidarr album match for {artist} – {album}")
        return None
    return results[0]

def lidarr_trigger_album_search(album_id, dry_run=False):
    if dry_run:
        print(f"  [DRY-RUN] Would trigger AlbumSearch for album ID {album_id}")
        return

    url = f"{LIDARR_URL}/api/v1/command?apikey={LIDARR_API_KEY}"
    payload = {"name": "AlbumSearch", "albumIds": [album_id]}
    r = requests.post(url, json=payload)
    if r.status_code == 201:
        print(f"  🔍 AlbumSearch triggered for album ID {album_id}")
    else:
        print(f"  ❌ AlbumSearch failed ({r.status_code}): {r.text}")

# ---------- MAIN ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Simulate actions without modifying Lidarr")
    args = parser.parse_args()
    dry_run = args.dry_run

    access_token = get_spotify_access_token()

    playlists = get_playlists(access_token)
    chosen = choose_playlists(playlists)
    if not chosen:
        print("No playlists selected, exiting.")
        return

    print("\nBuilding Plex library index...")
    plex_have = get_plex_tracks()
    print(f"Plex has {len(plex_have)} tracks.\n")

    seen_artists = set()
    seen_albums = set()

    for pl in chosen:
        print(f"\n=== Playlist: {pl['name']} ===")
        tracks = get_playlist_tracks(access_token, pl["id"])
        print(f"  {len(tracks)} tracks in playlist.")

        for item in tracks:
            t = item.get("track")
            if not t:
                continue

            track_name = t.get("name", "").strip()
            album_name = t.get("album", {}).get("name", "").strip()
            artists = [a["name"].strip() for a in t.get("artists", []) if a.get("name")]

            if not track_name or not album_name or not artists:
                continue

            primary_artist = artists[0]
            key = (primary_artist.lower(), album_name.lower(), track_name.lower())

            if key in plex_have:
                continue

            print(f"\nMissing in Plex: {primary_artist} – {album_name} – {track_name}")

            for artist_name in split_artists(primary_artist):
                if artist_name.lower() not in seen_artists:
                    print(f"  Checking/adding artist in Lidarr: {artist_name}")
                    artist_obj = lidarr_artist_lookup(artist_name)
                    if artist_obj:
                        lidarr_add_artist(artist_obj, dry_run=dry_run)
                    seen_artists.add(artist_name.lower())
                    time.sleep(0.3)

            album_key = f"{primary_artist.lower()}::{album_name.lower()}"
            if album_key not in seen_albums:
                print(f"  Looking up album in Lidarr: {album_name}")
                album_obj = lidarr_album_lookup(primary_artist, album_name)
                if album_obj:
                    lidarr_trigger_album_search(album_obj["id"], dry_run=dry_run)
                seen_albums.add(album_key)
                time.sleep(0.5)

if __name__ == "__main__":
    main()
