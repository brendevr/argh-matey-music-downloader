import yt_dlp

from playlists import Playlist, Song
from youtube import video_id_from_url, ydl_options

UNAVAILABLE_TITLES = ("[Private video]", "[Deleted video]")
IMPORTED_VIDEOS = "Imported Videos"


def fetch_playlist(url: str) -> Playlist:
    """Read a YouTube playlist without downloading it. Each video becomes a song with its id already known."""
    with yt_dlp.YoutubeDL(ydl_options(skip_download=True, extract_flat="in_playlist")) as ydl:
        info = ydl.extract_info(url, download=False)

    entries = info.get("entries")
    if entries is None:
        raise ValueError("That link is not a YouTube playlist.")

    name = info.get("title") or "YouTube Playlist"
    songs = []
    for entry in entries:
        if not entry or not entry.get("id") or entry.get("title") in UNAVAILABLE_TITLES:
            continue
        artist = entry.get("channel") or entry.get("uploader") or ""
        songs.append(Song(name, entry.get("title") or entry["id"], artist, [entry["id"]], len(songs)))

    if not songs:
        raise ValueError("The playlist has no available videos.")
    return Playlist(name, songs)


def fetch_video(url: str) -> Playlist:
    video_id = video_id_from_url(url)
    if video_id is None:
        raise ValueError("That is not a YouTube video link.")

    with yt_dlp.YoutubeDL(ydl_options(skip_download=True)) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)

    song = Song(
        IMPORTED_VIDEOS,
        info.get("title") or video_id,
        info.get("channel") or info.get("uploader") or "",
        [video_id],
    )
    return Playlist(IMPORTED_VIDEOS, [song])
