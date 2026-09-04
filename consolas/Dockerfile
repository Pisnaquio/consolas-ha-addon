# syntax=docker/dockerfile:1

FROM ghcr.io/home-assistant/base:latest

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=0.1.19

LABEL \
  io.hass.name="Consolas" \
  io.hass.description="Consolas Home Assistant add-on" \
  io.hass.version="${BUILD_VERSION}" \
  io.hass.type="app" \
  io.hass.arch="${BUILD_ARCH}"

ENV \
  CONSOLAS_HOST="0.0.0.0" \
  CONSOLAS_PORT="8788" \
  CONSOLAS_DATA_DIR="/data" \
  CONSOLAS_STATIC_DIR="/app/web" \
  CONSOLAS_APP_VERSION="${BUILD_VERSION}" \
  PYTHONUNBUFFERED="1"

WORKDIR /app

RUN apk add --no-cache bash ca-certificates python3 py3-requests sqlite-libs tzdata

COPY web/ /app/web/
COPY server/ /app/server/
COPY agents/ /app/agents/
COPY auction_search_config.py /app/auction_search_config.py
COPY buscador_bavastro.py /app/buscador_bavastro.py
COPY buscador_consolas_bavastro.py /app/buscador_consolas_bavastro.py
COPY buscador_consolas_castells.py /app/buscador_consolas_castells.py
COPY run.sh /run.sh

RUN chmod 0755 /run.sh \
  && chmod 0755 /app/agents/auction-watch/scripts/run_watch.py \
  && chmod 0755 /app/agents/auction-watch/scripts/run_watch_if_due.py \
  && chmod 0755 /app/agents/auction-watch/scripts/run_watch_scheduler_loop.sh

EXPOSE 8788

CMD ["/run.sh"]
