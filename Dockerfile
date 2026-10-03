FROM fedora:40
WORKDIR /bot
ENV PYTHONUNBUFFERED=1
RUN dnf -y install git bash wget curl python3-pip procps-ng psmisc && dnf clean all
RUN wget -q "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-n7.0-latest-linux64-gpl-7.0.tar.xz" && \
    tar -xJf ffmpeg-*.tar.xz && cp -a */bin/* /usr/bin/ && rm -rf ffmpeg-*
COPY . .
RUN useradd -u 1000 -m botuser && chown -R botuser:botuser /bot
USER botuser
RUN pip3 install --user --no-cache-dir -r requirements.txt
CMD ["bash", "start.sh"]
HEALTHCHECK --interval=30s --timeout=5s \
  CMD python3 -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/')" || exit 1
