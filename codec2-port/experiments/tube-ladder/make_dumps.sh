#!/usr/bin/env bash
# make_dumps.sh — run c2sim on the corpus in the two experiment conditions and
# collect per-frame parameter dumps + the reference phase0 synthesis (-o).
#
#   uq    : c2sim --lpc 10 --phase0 --postfilter --lpcpf
#           10 ms grid, UNQUANTISED LSP/Wo/E (decode-path params dumped as
#           _lsp_/_ak_/_qmodel); clean attribution of ladder effects.
#   q1300 : c2sim --rate 1300
#           full 1300-style quantisation: scalar Wo/E, LSP scalar quantisers,
#           decimate 4 (40 ms) + interpolation back to the 10 ms grid, lpcpf.
#           The dumped _lsp_/_ak_/_qmodel are the DECODED per-10ms-subframe
#           values actually used by synthesis — the real P1/P2 condition.
#
# Reference output <utt>_ref.raw is c2sim's own synthesis with the same flags.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/build/codec2/build_host/src"
RAW="$HERE/build/codec2/raw"
UTTS=(hts1a hts2a ve9qrp_10s)

for utt in "${UTTS[@]}"; do
  for cond in uq q1300; do
    d="$HERE/build/dumps/$cond/$utt"
    mkdir -p "$d"
    if [ "$cond" = uq ]; then
      FLAGS=(--lpc 10 --phase0 --postfilter --lpcpf)
    else
      FLAGS=(--rate 1300)
    fi
    "$BIN/c2sim" "$RAW/$utt.raw" "${FLAGS[@]}" \
        --dump "$d/$utt" -o "$d/${utt}_ref.raw" >"$d/c2sim.log" 2>&1
    python3 "$HERE/dump_params.py" "$d/$utt" "$d/$utt.npz"
  done
done
echo "dumps done"
