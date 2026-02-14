# commands

You can use `yt-dlp` to download YouTube videos:

### Install yt-dlp (if not installed)

```bash
# Mac
brew install yt-dlp

# Or with pip
pip install yt-dlp
```

### Download the video

```bash
# Best quality video + audio
yt-dlp "https://www.youtube.com/watch?v=J-ntsk7Dsd0"

# Or specify output filename
yt-dlp -o "trading_video.mp4" "https://www.youtube.com/watch?v=J-ntsk7Dsd0"

# Download best mp4 format (compatible with most players)
yt-dlp -f "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]" "https://www.youtube.com/watch?v=J-ntsk7Dsd0"
```

### Other useful options

```bash
# List available formats
yt-dlp -F "https://www.youtube.com/watch?v=J-ntsk7Dsd0"

# Download specific format (e.g., 720p)
yt-dlp -f 22 "https://www.youtube.com/watch?v=J-ntsk7Dsd0"

# Download audio only
yt-dlp -x --audio-format mp3 "https://www.youtube.com/watch?v=J-ntsk7Dsd0"
```

For a quick 4-minute demo video, download just a portion:

```bash
# Download first 4 minutes only
yt-dlp --download-sections "*0:00-4:00" -o "demo_video.mp4" "https://www.youtube.com/watch?v=J-ntsk7Dsd0"
```

Or if that video is longer and you want a specific 4-minute segment:

```bash
# Download minutes 2:00 to 6:00 (4 min segment)
yt-dlp --download-sections "*2:00-6:00" -o "demo_video.mp4" "https://www.youtube.com/watch?v=J-ntsk7Dsd0"
```

### Alternative: Download full then trim with ffmpeg

```bash
# Download full video
yt-dlp -o "full_video.mp4" "https://www.youtube.com/watch?v=J-ntsk7Dsd0"

# Trim to first 4 minutes
ffmpeg -i full_video.mp4 -t 00:04:00 -c copy demo_video.mp4
```

### Then upload to ChartSeek

Once downloaded, upload `demo_video.mp4` to ChartSeek at http://localhost:3000 for indexing and search demo.

##
```
ffmpeg -i demo_video.mp4.webm -c:v libx264 -c:a aac demo_video.mp4
```
