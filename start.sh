#!/bin/bash
set -e
mkdir -p downloads thumbs watermarks logs fonts bot/fonts bin

if [ ! -f ./bin/ffmpeg ] || [ ! -f ./bin/ffprobe ]; then
    echo "Downloading FFmpeg with SVT-AV1 support..."
    wget -q "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz" -O /tmp/ffmpeg.tar.xz
    echo "Extracting FFmpeg..."
    tar -xf /tmp/ffmpeg.tar.xz -C /tmp/
    mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffmpeg ./bin/ffmpeg
    mv /tmp/ffmpeg-master-latest-linux64-gpl/bin/ffprobe ./bin/ffprobe
    chmod +x ./bin/ffmpeg ./bin/ffprobe
    rm -rf /tmp/ffmpeg.tar.xz /tmp/ffmpeg-master-latest-linux64-gpl
    echo "FFmpeg ready with SVT-AV1 support!"
else
    echo "FFmpeg already present, skipping download."
fi

python3 -m bot
