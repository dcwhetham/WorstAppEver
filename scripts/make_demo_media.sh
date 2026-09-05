#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Generate a demo archive of real, playable media so the dashboard has actual
# content to render — covers, galleries, video seeking, ZIP bundles and the
# duplicate report all need real bytes, not placeholder rows.
#
# Everything is synthesised locally with ffmpeg. Nothing is downloaded.
#
#   ./scripts/make_demo_media.sh [archive_dir]
#
# Then index it:
#   curl -X POST localhost:8000/api/scan/sync
# ---------------------------------------------------------------------------
set -euo pipefail

ARCHIVE="${1:-archive}"

command -v ffmpeg >/dev/null || { echo "ffmpeg is required" >&2; exit 1; }

# handle:photos:videos:hue — the hue spreads the generated gradients so cards are
# visually distinguishable in a grid rather than a wall of identical squares.
ACCOUNTS=(
    "aurora.films:14:3:190"
    "neon.district:11:2:310"
    "kestrel.archive:8:1:35"
    "halcyon.studio:6:1:270"
    "vantablack.co:9:2:150"
)

photo() { # path hue index
    local out=$1 hue=$2 i=$3
    local shift=$(( (hue + i * 11) % 360 ))
    ffmpeg -loglevel error -y \
        -f lavfi -i "gradients=s=1080x1350:c0=0x101820:c1=0x67e8f9:n=3:d=1:speed=0.1" \
        -vf "hue=h=${shift}:s=1.1,drawtext=text='${i}':fontsize=190:fontcolor=white@0.22:x=(w-tw)/2:y=(h-th)/2" \
        -frames:v 1 -q:v 4 "$out"
}

video() { # path hue index
    local out=$1 hue=$2 i=$3
    local shift=$(( (hue + i * 47) % 360 ))
    # 6 seconds is long enough that dragging the scrubber is a real test of the
    # backend's Range handling rather than a single-buffer read.
    ffmpeg -loglevel error -y \
        -f lavfi -i "testsrc2=s=1280x720:r=24:d=6" \
        -vf "hue=h=${shift}" \
        -c:v libx264 -preset veryfast -pix_fmt yuv420p -movflags +faststart "$out"
}

for entry in "${ACCOUNTS[@]}"; do
    IFS=: read -r handle photos videos hue <<<"$entry"
    mkdir -p "$ARCHIVE/$handle/photos" "$ARCHIVE/$handle/videos"
    echo "==> $handle ($photos photos, $videos videos)"

    for ((i = 1; i <= photos; i++)); do
        day=$(printf "%02d" $(((i % 27) + 1)))
        photo "$ARCHIVE/$handle/photos/2026-08-${day}_${handle}_$(printf '%03d' "$i").jpg" "$hue" "$i"
    done

    for ((i = 1; i <= videos; i++)); do
        day=$(printf "%02d" $(((i * 7 % 27) + 1)))
        video "$ARCHIVE/$handle/videos/2026-08-${day}_${handle}_$(printf '%03d' "$i").mp4" "$hue" "$i"
    done
done

# A byte-identical copy under a different name, so the dedup path and the
# duplicate report have something real to find.
cp "$ARCHIVE/aurora.films/photos/2026-08-02_aurora.films_001.jpg" \
   "$ARCHIVE/aurora.films/photos/2026-08-02_aurora.films_001_copy.jpg"

echo
echo "done: $(find "$ARCHIVE" -type f \( -name '*.jpg' -o -name '*.mp4' \) | wc -l) files in $ARCHIVE"
