FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_MOCK=false
ENV VITE_API_MOCK=$VITE_API_MOCK
ARG VITE_ALLOW_ONLINE_GEOCODER=false
ENV VITE_ALLOW_ONLINE_GEOCODER=$VITE_ALLOW_ONLINE_GEOCODER
RUN npm run build

FROM alpine:3.20 AS maps
RUN apk add --no-cache curl coreutils
COPY offline-map /offline-map
# Windows checkouts may contain CRLF; strip it before running the shell script.
# Keep the file in place: fetch.sh resolves cities.geojson relative to itself.
RUN sed -i 's/\r$//' /offline-map/fetch.sh \
    && sh /offline-map/fetch.sh /tiles

FROM nginx:1.27-alpine
COPY nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
COPY --from=maps /tiles /usr/share/nginx/html/tiles
ENV API_UPSTREAM=http://api:8000
EXPOSE 80
