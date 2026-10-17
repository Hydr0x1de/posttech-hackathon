#!/bin/sh
set -eu

OUT_DIR="${1:-public/tiles}"
PMTILES_VERSION="${PMTILES_VERSION:-1.31.2}"
KZ_BBOX="46.4,40.5,87.4,55.5"
HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

case "$(uname -s)" in
  Linux*) OS=Linux; EXT=tar.gz; BIN=pmtiles ;;
  Darwin*) OS=Darwin; EXT=zip; BIN=pmtiles ;;
  MINGW* | MSYS* | CYGWIN*) OS=Windows; EXT=zip; BIN=pmtiles.exe ;;
  *) echo "Неизвестная ОС: $(uname -s)" >&2; exit 1 ;;
esac
case "$(uname -m)" in
  x86_64 | amd64) ARCH=x86_64 ;;
  aarch64 | arm64) ARCH=arm64 ;;
  *) echo "Неизвестная архитектура: $(uname -m)" >&2; exit 1 ;;
esac

SEP=_
[ "$OS" = Darwin ] && SEP=-
ASSET="go-pmtiles${SEP}${PMTILES_VERSION}_${OS}_${ARCH}.${EXT}"
curl -fsSL -o "$WORK/pmtiles.$EXT" "https://github.com/protomaps/go-pmtiles/releases/download/v${PMTILES_VERSION}/${ASSET}"
if [ "$EXT" = zip ]; then unzip -q -o "$WORK/pmtiles.zip" -d "$WORK"; else tar -xzf "$WORK/pmtiles.tar.gz" -C "$WORK"; fi
PM="$WORK/$BIN"

SRC=""
for days in 1 2 3 4 5 6 7 8 9 10; do
  day="$(date -u -d "-$days day" +%Y%m%d 2>/dev/null || date -u -v-"$days"d +%Y%m%d)"
  if curl -fsI "https://build.protomaps.com/$day.pmtiles" >/dev/null 2>&1; then
    SRC="https://build.protomaps.com/$day.pmtiles"
    break
  fi
done
[ -n "$SRC" ] || { echo "Не нашёл свежую сборку на build.protomaps.com" >&2; exit 1; }
echo "Источник: $SRC"

mkdir -p "$OUT_DIR"
"$PM" extract "$SRC" "$WORK/world.pmtiles" --maxzoom=5 --download-threads=8
"$PM" extract "$SRC" "$WORK/kz.pmtiles" --bbox="$KZ_BBOX" --minzoom=6 --maxzoom=10 --download-threads=8
"$PM" merge "$WORK/world.pmtiles" "$WORK/kz.pmtiles" "$OUT_DIR/kz-base.pmtiles"
"$PM" extract "$SRC" "$OUT_DIR/kz-cities.pmtiles" --region="$HERE/cities.geojson" --minzoom=11 --maxzoom=15 --download-threads=8

ls -l "$OUT_DIR"
