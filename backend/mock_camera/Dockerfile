FROM bluenviron/mediamtx:latest-ffmpeg

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8554

ENTRYPOINT ["/entrypoint.sh"]
