# wg015_vcd — hardware-in-the-loop VCD analyzer for the WG015 port

Analyzes logic-analyzer VCD exports of the K1921VG015 bitbang LS-USB port:
decodes the 1.5 Mbit/s bus (D+/D−) and the zero-intrusiveness marker channel
(PLAN.md §Р10: DBG0 grid toggles via `DATAOUTTGL` — one toggle right after each
RX sample and at TX cell boundaries; first toggle after the D− falling edge is
taken as ISR entry). Feeds the numbers back into the pad map of
`doc/wg015/ledger_static.md` (WGDELAY_* knobs in `usb_port_wg015.h`).

Python 3 stdlib only, no pip deps. Every number is reported in **ns and
48 MHz cycles** (1 cyc = 20.833 ns; 32 cyc/bit).

```
wg015vcd.py {decode|rx|tx|bench} capture.vcd [options]
```

Exit codes: `0` ok, `1` gate violated, `2` malformed VCD / usage / no traffic.

## Probe / capture notes

- Channels: D+ (C0), D− (C1), DBG0 (**B2** default; DPU/C2 doubles as DBG0 only
  inside driven-bus windows — see §Р10), optionally DBG1 (C3, events).
- **Trigger on D− falling edge** (packet SYNC start = J→K; keepalive SE0 also
  starts with a D− fall).
- Sample rate **≥100 MS/s**. At 100 MS/s the positional resolution is 10 ns ≈
  0.5 cyc — good enough for pad decisions; use 200–500 MS/s for sub-quarter-cycle
  eye positioning.
- Capture length **≥2–3 ms** so the window contains keepalives (1 ms grid) plus
  at least one full transaction; for excursion statistics capture as many
  packets as the LA buffer allows.
- Export as VCD. Channel names are matched by substring; `dp`/`d+`, `dn`/`d-`/`dm`,
  `dbg0`/`dbg` are auto-guessed (`dpu` is never mistaken for `dp`). Override with
  `--dp NAME --dn NAME --dbg NAME`; inspect with `--list-channels`.

## Iteration loop (which capture → which command → what to paste back)

| Phase / question | Capture | Command | Paste back into the ledger discussion |
|---|---|---|---|
| P1.3 IRQ entry gate (≤~55 cyc) | keepalives + any traffic, DBG0 on | `wg015vcd.py rx cap.vcd` | `== rx: ISR entry latency` block (min/med/max, ns + cyc) and the GATE line |
| P3 RX eye / TUNE sweep | host SETUP traffic, DBG0 grid mask on | `wg015vcd.py rx cap.vcd --gate-excursion <window>` | offset histogram, per-packet table (first/last/slope/excur), GATE lines; `--verbose` for the full per-bit series of a suspect packet |
| P3 drift (G1-(б) criterion) | long capture, many packets | `wg015vcd.py rx cap.vcd --json > rx.json` | `rx.slope_cyc_per_bit` and `rx.excursion_cyc` from the JSON |
| P4 TX cell exactness | enumeration attempt, DBG0 TX mask on | `wg015vcd.py tx cap.vcd --gate-turnaround 7.5` | `cell period` stats + histogram, per-packet table, turnaround stats, EOP SE0 width |
| P4 TX fronts (R12, coarse) | same, both D± probed | `wg015vcd.py tx cap.vcd` | `D+/D- edge-to-edge skew` block (real slopes need a scope; this is the LA-level estimate) |
| P1 benches (bench1..6 markers) | DBG0 only | `wg015vcd.py bench cap.vcd` | interval stats + histogram |
| Sanity: is the bus alive / CRC clean | any | `wg015vcd.py decode cap.vcd` | the packet table + notes |

Machine use: add `--json` — same content, one JSON object on stdout
(`gates.*.pass`, `pass`, per-packet arrays). Gate knobs: `--gate-entry 55`
(default, `0` disables), `--gate-excursion CYC`, `--gate-turnaround BT`.

## What the numbers mean

- **ISR entry latency**: D− falling edge → first DBG toggle inside the packet.
  With DBG1-style entry marking folded onto the analyzed channel this is the
  first-instruction toggle; with a pure DBG0 grid capture it is edge→first-sample,
  i.e. an upper bound on entry (subtract the catcher constant, ledger A1/A2).
- **Sample offset**: each grid toggle is mapped into the bit cell reconstructed
  from the host packet's own transitions (least-squares cell grid, tolerance
  ±1.5 % of 1.5 Mbit/s, hard fail beyond ±2.5 %). Offset = toggle − cell start,
  in cycles. The toggle sits one store after the actual `USB_SAMPLE` — constant
  offset, irrelevant for drift/excursion, subtract 1+G if you need the absolute
  sample point.
- **Drift slope**: least-squares slope of offset vs bit index, cycles/bit
  (32 × clock offset: 5000 ppm ↔ 0.16 cyc/bit).
- **Cumulative excursion**: max−min offset within one packet — the quantity the
  G1-(б) criterion bounds over ≤102-slot windows (PLAN §3).
- **TX cell period**: intervals between marker toggles inside device packets
  (device packets are identified by a token/data/handshake direction state
  machine; `--include-unknown` if your traffic confuses it).
- **Turnaround**: host EOP end (SE0→J) → device SYNC start (J→K), in bit-times
  (spec window 2–7.5 bt; response packets = leading gap < `--tx-gap-max`, 16 bt).

## Selftest

```
tools/wg015_vcd/selftest/run_selftest.sh
```

Generates synthetic captures (`make_test_vcd.py`: SETUP transactions +
keepalives + device ACK, with configurable device-clock ppm, entry latency,
marker jitter/drift/turnaround) and asserts the analyzer reproduces the
injected values: nominal, +5000 ppm, entry 40 vs 70 cyc (gate-55 pass/fail),
drift +0.05 cyc/bit, excursion gate firing, 10 ns quantization is tolerated,
malformed VCD → clean exit 2. Must print `0 failed`.

## Known limitations

- VCD is 2-level: real rise/fall times need a scope; the tool only reports
  D+/D− edge-to-edge skew (transients shorter than `--glitch-ns`, default 120 ns,
  are folded into the transition midpoint).
- Direction (host vs device) is inferred from PID protocol order; exotic traffic
  falls into `unknown` (excluded unless `--include-unknown`).
- One marker channel: if both RX-grid and TX masks are enabled at once the RX
  offset statistics of packets adjacent to TX will include TX toggles only if
  they land inside a host packet window (they don't in normal traffic).
- Bit-cell reconstruction needs ≥2 transitions per packet; an all-ones packet
  body (impossible after stuffing) is not a concern.
- Timescale, not sample rate, is read from the VCD; export with the LA's native
  resolution (don't downsample the export).
