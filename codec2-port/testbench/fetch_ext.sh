#!/usr/bin/env bash
# fetch_ext.sh — download the external (non-repo) corpus items.
#
# The pinned codec2 checkout ships raw/ (hts1a, hts2a, kristoff, ve9qrp_10s…).
# David Rowe's codec2 pages carry further ORIGINAL (uncoded) utterances that
# are not in the repo.  Only originals are useful here — the blog's per-mode
# A/B files are already vocoded and cannot be re-encoded meaningfully.
#
# Reachability is not guaranteed (proxy/offline): every fetch is best-effort
# and corpus.py drops what is missing, recording the fact in the manifest.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXT="$HERE/out/ext"
mkdir -p "$EXT"

get() { # get <dest> <url>
  if [ -s "$EXT/$1" ]; then echo "  have $1"; return; fi
  code=$(curl -sSL --max-time 60 -o "$EXT/$1" -w '%{http_code}' "$2" || echo 000)
  if [ "$code" = "200" ]; then
    echo "  got  $1  ($(stat -c %s "$EXT/$1") B)  <- $2"
  else
    echo "  MISS $1  (http $code)  <- $2"
    rm -f "$EXT/$1"
  fi
}

# https://www.rowetel.com/?page_id=452  — "Codec 2" landing page, originals
get mmt1.wav      https://www.rowetel.com/downloads/codec2/mmt1.wav
get hts2a_ext.wav https://www.rowetel.com/downloads/codec2/hts2a.wav
# https://www.rowetel.com/?p=6273 — "Codec 2 2200", "Original" link
get cq_ref.wav    https://www.rowetel.com/downloads/codec2/2200/cq_ref.wav

echo "fetch_ext: $(ls -1 "$EXT" | wc -l) file(s) in out/ext/"
