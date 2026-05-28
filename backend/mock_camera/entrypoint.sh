#!/bin/sh
set -e

# Start mediamtx in background
/mediamtx &
MTX_PID=$!

# Wait for mediamtx to be ready
sleep 2

# Generate a test pattern RTSP stream
# Uses testsrc with a moving pattern to simulate camera motion
# Publishes to rtsp://localhost:8554/mock-camera-1
ffmpeg -re -f lavfi -i testsrc=duration=-1:size=1280x720:rate=1 \
  -f lavfi -i sine=frequency=1000:duration=-1 \
  -pix_fmt yuv420p -c:v libx264 -preset ultrafast -tune zerolatency \
  -f rtsp rtsp://localhost:8554/mock-camera-1 &

wait $MTX_PID
