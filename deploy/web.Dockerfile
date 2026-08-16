# The deployed front door: the built dashboard, served by Caddy, which also
# proxies the API so both live on one origin.
#
# Built from the repository root, because it needs both frontend/ and the
# Caddyfile beside it.

FROM node:24.18.0-bookworm-slim AS build

WORKDIR /build

# Dependencies are installed from the lockfile alone first, so a change to
# application source does not invalidate the install layer.
COPY frontend/package.json frontend/package-lock.json frontend/.npmrc ./
RUN npm ci

COPY frontend/ ./
# tsc -b runs as part of this, so a type error fails the image build rather
# than shipping.
RUN npm run build


FROM caddy:2.10-alpine

COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /build/dist /srv

# Caddy's image already runs its process as root only to bind low ports; this
# listens on 8080, so it does not need to.
RUN adduser -S -u 10001 -G nogroup web \
    && chown -R web:nogroup /srv /config /data

USER web

EXPOSE 8080

CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
