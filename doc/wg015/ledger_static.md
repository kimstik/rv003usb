# WG015 static cycle ledger — rv003usb.S (DRAFT / first pass)

Branch `claude/wg015-bitbang-usb-port-bxuu7w`. Target K1921VG015 / BM-310S6.
Source: `/home/user/rv003usb/rv003usb/rv003usb.S` (line refs = `S:<n>`, state of this branch).
**This is a paper ledger.** Hardware benches (bench5 + LA) recalibrate B and G; the value
here is the f(B,G) formulas and the per-site pad map, not the absolute numbers.

## Timing model (research_core_irq.md §1, research_bm310.md §3)

| Item | Cost | Note |
|---|---|---|
| Any RV32IMC ALU / TCM lw / TCM sw | 1 | «Выполнение всех команд RV32IMC занимает один такт» |
| Taken branch, and any j / c.j / jal / jr | 1+B | B ∈ {0,1} (pipeline option 1 vs 2, unknown). Unconditional-jump = 1+B is an assumption (?) |
| Not-taken branch | 1 | |
| GPIO lw/sw over AHB (INDR/DATA, MASKLB=BSHR, OUTENSET/CLR, INTSTATUS, DEBUG_TICK_MARK) | 1+G | G unknown, expected 0–3. Assumes stores stall like loads; if writes are posted, sw may be 1+G' with G'<G (?) |
| PLIC MICC lw/sw (system bus, not GPIO AHB) | 1+P | P unknown; assume P≈G (?) |
| csrr (rdcycle) | 2 | pipeline drain, 2-stage |
| mret, trap-entry latency | (?) | no public numbers; excluded from counts (constants T_mret, T_irq) |
| `la sym` / `li big` | 2 | lui/auipc+addi; linker relaxation could make some 1 (?) |
| Fetch | ideal | I-cache hit assumed; flash-stall jitter is bench5's job, not this ledger's |

WG015 preprocessing facts used: `VOOXDELAY` = **empty**; `CH32V00x` not defined ⇒ V003 branch of
every `#if` except the `WG015` blocks; `USB_FAR_DISPATCH=0` (flash-resident .timecrit default);
`RV003_ADD_EXTI_MASK` assumed **off**; `XW_C_LBU/LHU/SB` → standard `lbu/lhu/sb` (1 cycle to TCM,
32-bit encodings — no cycle change under ideal fetch); `DEBUG_TICK_MARK` = 1 GPIO store = 1+G.

**nx6p3delay recompute** (S:45, `li n+1; 1: c.addi -1; c.bnez 1b`):
V003 = 6n+3. WG015 = `li`(1) + (n+1)·addi + n·taken(1+B) + 1·not-taken = **(2+B)·n + 3**
(prompt's (2+B)n+2 is off by one — the final not-taken bnez). n=2: 7+2B; n=3: 9+3B; n=7: 17+7B.

Bit budget: **32 cycles/bit** @48 MHz (LS 1.5 Mbit/s).

---

## A. RX paths

Convention: a "slot" runs label-top → label-top (includes its own DEBUG_TICK_MARK + sample lw).
Pad@ = 1-cycle instructions needed at (B,G)=(1,1).

| # | Path | Range | f(B,G) | (0,0) | (1,1) | (1,2) | Budget | Δ@(1,1) | Pad@(1,1) |
|---|---|---|---|---|---|---|---|---|---|
| A1 | ISR entry → 1st sample (SE0 check lw done) | S:101–106 | 6+G (+T_irq) | 6 | 7 | 8 | — | — | 0 (phase const) |
| A2 | ISR entry → 1st catcher sample done | S:101–146 | 19+3G (+T_irq) | 19 | 22 | 25 | — | — | 0 (phase const) |
| A3 | Edge-catcher slot (no edge) | S:145–159 | 3+G | 3 | 4 | 5 | 4 (=32/8) | 0 | **0** (at G=0: 1/slot) |
| A4 | Catcher full window + `c.j syncout` | S:145–161 | 8(3+G)+1+B | 25 | 34 | 42 | ~32 | +2 | note only |
| A5 | preamble_loop iter, normal (s0==0, on-time) | S:179–201 | 15+3G+4B | 15 | 22 | 25 | 32 (?) | −10 | 10 |
| A6 | preamble_loop iter, slow (s0≠0, retime) | S:179–201 | 14+3G+4B | 14 | 21 | 24 | 31 | −10 | (A5−1) |
| A7 | packet_type_loop iter (bnez taken) | S:254–299 | 24+2G+B (?la) | 24 | 27 | 29 | 32 | −5 | 5 |
| A8 | header→bit_process transition, token path (beqz data_crc NT) | S:306–347 | last-iter tail +8 | — | — | — | — | — | see A9 |
| A9 | same, DATA path (beqz taken) | S:306–347 | tail +5+B | — | — | — | — | skew **3−B** | 2 on data path |
| A10 | bit_process→handle_zero_bit→back (mid-byte) | S:347–405 | 22+2G+2B | 22 | 26 | 28 | 32 | −6 | 6 |
| A11 | bit_process→handle_zero_bit→is_end_of_byte→back | S:347–394,341–347 | 20+2G+2B | 20 | 24 | 26 | 32 | −8 | 8 (=6+2 EOB skew) |
| A12 | bit_process→handle_one_bit→back (mid-byte) | S:347–436 | 21+2G+2B | 21 | 25 | 27 | 32 | −7 | 7 (=6+1 skew vs A10) |
| A13 | handle_one_bit EOB variant | S:410–427,341–347 | 19+2G+2B | 19 | 23 | 25 | 32 | −9 | 9 |
| A14 | bit-stuff interval (a): bit_process top → not_is_eob…stuffed sample top | S:347–425,439–446 | 17+2G+3B | 17 | 22 | 24 | 32 | −10 | 10 |
| A15 | interval (a), EOB variant (HANDLE_EOB in stuff path) | S:439–446 | 19+2G+2B | 19 | 23 | 25 | 32 | −9 | 9 (skew vs A14 = 2−B) |
| A16 | bit-stuff interval (b): not_is_eob top → bit_process top | S:446–467 | 17+2G+3B (incl nx(2)=7+2B) | 17 | 22 | 24 | 32 | −10 | 10 |
| A17 | se0_complete → yes_check_tokens → IN dispatch (addr≠0) | S:470–535 | 26+B (?la×2) | 26 | 27 | 27 | none (turnaround budget) | — | 0 |
| A18 | ISR-head SE0 → handle_se0_keepalive, in-range path to `j ret_from_se0` | S:142,737–797 | 20+B (csrr=2) | 20 | 21 | 21 | none | — | 0 |
| A19 | ret_from_se0 → mret done (incl. PLIC ack) | S:613–654 | 18+B+G+2P (+T_mret) (?) | 18 | 22 | 25 | none | — | 0 |

Notes:
- A3: **the V003 catcher structure is already exact at (B,G)=(1,1)**; at G=0 each slot needs +1
  (VOOXDELAY sites S:145–159); at G=2 each slot is 1 over (remove nothing — accept 8×5=40 window
  or drop to 6 slots) (?).
- A5/A6: retime step (slow−normal) is now **1 cycle** (V003: 2, «6 or 8 cycles» S:198). The
  preamble PLL corrects half as fast per iteration — probably fine, flag for bench (?). Budget 32
  assumes 1 iter = 1 preamble bit (?).
- A7: `la t0,0x80` counted as 2 (?); if relaxed to 1, subtract 1.
- A9: data path skips `c.li a4; c.li a3; nop` (S:322–325) — pad at data_crc VOOXDELAY cluster
  S:330–332 with 3−B nops **on the data side only** (restructure: move pad above `data_crc` into
  the fall-through side is the V003 trick; on WG015 put 3−B after the label reached only by taken? —
  simplest: add 3−B nops immediately after `data_crc:` and 0 before, then re-equalize with S:325).
- A11/A13: EOB slot is uniformly **2 cycles short** of its mid-byte twin (the `sb`+`addi` replace
  5 tail cycles). Existing pad site: VOOXDELAY at **S:343** (inside is_end_of_byte) — needs 2 nops
  there, independent of B,G.
- A14–A16: bit-stuff spans 2 bit-times; both halves are −10 at (1,1). (b) has its own delay knob
  (nx6p3delay(2,a0) S:464: each +1 of n = +(2+B) cycles).

### RX pad-site map (where, at (B,G)=(1,1))

| Site (nearest label / lines) | Serves | Cycles needed @(1,1) | f(B,G) |
|---|---|---|---|
| VOOXDELAY ×8, S:145–159 (edge catcher) | A3 | 0 | 1−G per slot (clamp ≥0) |
| VOOXDELAY S:183/188/197 + `j 1f` pads S:187/190 (preamble_loop) | A5/A6 | 10 | 17−3G−4B |
| 12×VOOXDELAY cluster S:216–228 + c.nop S:214 (done_preamble) | preamble→packet_type phase | ~0 (block identical to V003: 5 cycles; V003 tolerance «−4..+6» S:229–231) (?) | recheck on LA |
| c.nop S:267 + c.nop S:286 (packet_type_loop) | A7 | 5 | 8−2G−B |
| `.word nop` S:325 + VOOXDELAY S:330–332 (data_crc) | A9 skew | 2 (data path) | 3−B |
| VOOXDELAY S:343 (is_end_of_byte) | A11/A13/A15 EOB skew | 2 | 2 |
| VOOXDELAY S:383, S:395–398 + c.nop S:401/402/404 (handle_zero_bit tail) | A10 | 6 | 10−2G−2B |
| VOOXDELAY S:424, S:428–430 + c.nop S:432/433/435 (handle_one_bit tail) | A12 | 7 | 11−2G−2B |
| handle_bit_stuff head S:439–443 (before not_is_eob sample; no existing pad — new site) | A14 | 10 | 15−2G−3B |
| c.nop S:463 + nx6p3delay n S:464 + VOOXDELAY S:466 (not_is_eob tail) | A16 | 10 | 15−2G−3B |

---

## B. TX paths

### B1. pre_and_tok_send_inner_loop (S:895–933)

Store = `sw s1, BSHR_OFFSET(a5)` at S:927 (MASKLB window). "Store index" = cycles from loop top
to the cycle the sw **issues** (0-based).

| Path | f(B,G) | (0,0) | (1,1) | (1,2) | Budget | Δ@(1,1) | Store index f(B) |
|---|---|---|---|---|---|---|---|
| zero bit (flip: bnez NT → xor,li → sw) | 19+G+3B | 19 | 23 | 24 | 32 | −9 | **7** |
| one bit (bnez taken → sw) | 17+G+4B | 17 | 22 | 23 | 32 | −10 | **5+B** |

**STORE SKEW = 2−B** (zero=7, one=5+B). At B=0 the edge lands 2 cycles early on every
non-flip bit; at B=1, 1 cycle early. On V003 the taken-branch cost made these equal by design
(".balign 4 // Deliberately unaligned for timing purposes", S:924–925). **Fix required**: the taken
path jumps straight to the shared `pre_and_tok_send_one_bit` label, so there is no place to pad
without restructuring — recommend a stub: `c.bnez a3, 1f; …; 1: c.nop ×(2−B); j/fall pre_and_tok_send_one_bit`
or equivalently give the one-path its own copy of the sw. Pad = 2−B one-path-only cycles.
Slot-length pad (both paths, after skew fix): 13−G−3B, at existing sites nx6p3delay n (S:932,
each +1 n = +(2+B)) + c.nop S:932.

### B2. send_inner_loop (S:977–1058)

Store only on flips: `sw s1, BSHR` S:1038 (zero path) — one-bit path has **no store** (NRZI).
So the invariant is: slot length = 32 exactly, and store index constant across zero-slots.

| Path | f(B,G) | (0,0) | (1,1) | (1,2) | Budget | Δ@(1,1) | Store index |
|---|---|---|---|---|---|---|---|
| zero bit, mid-byte | 18+G+3B | 18 | 22 | 23 | 32 | −10 | 7+B (issue after mv,andi,beqz-taken,srli,slli,srai,xor) |
| one bit, mid-byte | 17+3B | 17 | 21 | 21 | 32 | −11 | (no store) |
| zero bit + load_next_byte | 18+G+2B | 18 | 21 | 22 | 32 | −11 | 7+B |
| one bit + load_next_byte | 17+2B | 17 | 19 | 19 | 32 | −13 | (no store) |
| one bit → insert_stuffed_bit → back (covers 2 bits) | 30+G+7B | 30 | 38 | 39 | 64 | −26 | data-bit: none; stuffed-bit store index = 24+4B |

Skews:
- zero − one (mid-byte) = **1+G**: pad the one-path exclusively by 1+G — site: between
  `c.beqz a4, insert_stuffed_bit` (S:1016) and `c.j cont_after_jump` (S:1017); safe because the
  one-path never stores.
- mid-byte tail (`c.beqz NT; c.j 1f; c.j loop` = 3+2B) vs byte-boundary tail
  (`c.beqz taken; lbu; c.addi` = 3+B): boundary is **B short** — pad B after `c.addi a0,1`
  (S:983, load_next_byte). `lbu` counted 1 assuming data buffer in TCM — **if `a0` points at
  flash constants (descriptor sends) this is variable-latency** (redteam finding; not a pad,
  a placement rule) (?).
- Slot pad placement: pad **after** the store, not in the 5×VOOXDELAY head (S:999–1003), so the
  store index stays 7+B; tail sites: after S:1045 (`c.xor a2,a3` in send_zero_bit) and the
  `c.j 1f;1:` at S:1057. Zero-slot pad = 14−G−3B (10 @1,1); one-slot total = 15−3B
  (= zero pad + (1+G) skew pad).

### B3. insert_stuffed_bit (S:1124–1131)

Stuffed-bit store must land 32 cycles after the (absent) data-1 edge grid point, i.e. at
index 32+(7+B)=39+B of the 64-cycle double slot. Currently 24+4B (28 @B=1).
- Pad **before** the store: 15−3B (12 @(1,1)) — knob: nx6p3delay n at S:1125 (n=3 → raise;
  each +1 n = +(2+B)) + c.nop ×2 S:1128–1129.
- Pad **after** (to make the double slot 64): 19−G−4B (14 @(1,1)) — this rides the shared
  cont_after_jump tail, so it must come from the stuff path itself: site after `sw` S:1130,
  before `c.j send_end_bit_complete`.
- V003 comment "222 or 226 (not 224) … off by 2" (S:1122) — the ±2 wobble is inherited; re-verify
  on LA after re-pad (?).

### B4. TX SE0 / J-park / release (WG015 block S:1082–1093)

| Segment | Range | f(B,G) | (0,0) | (1,1) | (1,2) | Target |
|---|---|---|---|---|---|---|
| last-bit tail → SE0 store done (zero-bit final slot tail: 4 + beqz-taken + beqz-taken + nx(2) + li + sw) | S:1050–1084 | 15+4B+G | 16 | 20 | 21 | ≈ one bit gap (?) |
| SE0 width (SE0 sw done → J sw done): nx(7) + li + sw | S:1086–1089 | 19+7B+G | 20 | 27 | 28 | 64 (2 bits; V003 ships ≈48 (?)) |
| J-park hold (J sw done → OUTENCLR sw done): li + sw | S:1092–1093 | 2+G | 2 | 3 | 4 | V003 equiv ≈8 |
| epilogue to `ret` | S:1115–1119 | 5+B (+jalr) | 5 | 6 | 6 | — |

SE0 width knob: nx6p3delay n at S:1086. (2+B)n+5+G = target ⇒ **n = (target−5−G)/(2+B)**:
match-V003(≈48): n≈14 @(1,1); nominal 2-bit 64: n≈19–20 @(1,1) (?) — decide from LA against a
reference V003 capture.
Release: WG015 is ~6−G cycles / 6 instructions shorter than the V003 CFGLR rewrite (TUNE comment
S:1094) — pad only if J-hold before release matters on the analyzer.

---

## C. Turnaround: usb_send_data entry → bus drive → first preamble store

C call overhead excluded; counts start at `usb_send_data:` (S:821).

| Milestone | Range | WG015 f(B,G) | (0,0) | (1,1) | (1,2) | V003 (insn count) | Δ insns |
|---|---|---|---|---|---|---|---|
| entry → bus driven K (OUTENSET done; WG015 = preset MASKLB then OUTENSET) | S:821–835 | 9+2G | 9 | 12 | 13 | 16 | **−7** |
| entry → first preamble store done (token path, `c.bnez a2` taken) | S:821–927 | 30+3G+2B | 30 | 35 | 38 | 37 (+V003 jump penalties ≈ 40–45 (?)) | −7 |
| first-K width (OUTENSET done → first flip store done) | S:836–927 | 21+G+2B | 21 | 24 | 25 | ≈21+V003 penalties ≈25 (?) | ~0 |
| poly path extra (bnez NT + li t0 + li a2 vs taken) | S:873–875 | +4−B skew | +4 | +3 | +3 | same structure | 0 |
| usb_send_empty prefix | S:815–819 | 5 | 5 | 5 | 5 | 5 | 0 |

- The **acquire is 7 instructions shorter** than V003 (TUNE comment S:836): the bus turns around
  earlier (good for the 2–6.5-bit-time response window), but the pre-preamble K state is held a
  correspondingly different time. If the LA shows the first sync K short vs a V003 reference,
  pad at S:836 (post-OUTENSET, pre-`li t1`): WGDELAY_TURNAROUND ≈ 7−2G to restore the V003 shape,
  or 32−(21+G+2B) = 8 @(1,1) for a full-width first K bit (?).
- Poly/no-poly skew (4−B) predates this port (same on V003); it lands before the loop, shifting
  the whole packet by a constant — harmless (?).

---

## D. Recommended WGDELAY macros (usb_port header style)

```c
// ---- calibration parameters (bench5 recalibrates; paper defaults) ----------
#define WG_B 1   // taken-branch/jump penalty: 0 or 1 (BM-310 pipeline opt 1 vs 2)
#define WG_G 1   // extra cycles per GPIO AHB lw/sw (expected 0..3)
#define WG_P WG_G // PLIC MICC access extra cycles (assumed = G) (?)
#define WG_CLAMP0(x) ((x) > 0 ? (x) : 0)

// ---- RX --------------------------------------------------------------------
#define WGDELAY_EDGE_SLOT   WG_CLAMP0(1 - WG_G)          // per catcher slot, 8x  [S:145-159]
#define WGDELAY_PREAMBLE    (17 - 3*WG_G - 4*WG_B)       // preamble_loop iter    [S:183/188/197]
#define WGDELAY_PKTTYPE     (8  - 2*WG_G - WG_B)         // packet_type_loop iter [S:267/286]
#define WGDELAY_DATACRC_SKEW (3 - WG_B)                  // data-PID path only    [S:325-332]
#define WGDELAY_RX0         (10 - 2*WG_G - 2*WG_B)       // handle_zero_bit tail  [S:395-404]
#define WGDELAY_RX1         (11 - 2*WG_G - 2*WG_B)       // handle_one_bit tail   [S:428-435]
#define WGDELAY_RX_EOB      2                            // is_end_of_byte        [S:343]
#define WGDELAY_STUFF_A     (15 - 2*WG_G - 3*WG_B)       // handle_bit_stuff head [S:439-443, new site]
#define WGDELAY_STUFF_AEOB  WG_CLAMP0(WGDELAY_STUFF_A - (2 - WG_B)) // EOB-in-stuff variant [S:444]
#define WGDELAY_STUFF_B     (15 - 2*WG_G - 3*WG_B)       // not_is_eob tail; use nx n [S:463-466]

// ---- TX --------------------------------------------------------------------
#define WGDELAY_PRETOK      (13 - WG_G - 3*WG_B)         // pretok slot; nx n knob [S:932]
#define WGDELAY_PRETOK_SKEW (2 - WG_B)                   // one-bit path stub (restructure) [S:914/926]
#define WGDELAY_TX0         (14 - WG_G - 3*WG_B)         // send zero slot, AFTER store [S:1045/1057]
#define WGDELAY_TX1_SKEW    (1 + WG_G)                   // one-bit path only     [S:1016-1017]
#define WGDELAY_LNB         (WG_B)                       // load_next_byte tail   [S:983]
#define WGDELAY_STUFFTX_PRE  (15 - 3*WG_B)               // insert_stuffed_bit pre-store; nx n [S:1125]
#define WGDELAY_STUFFTX_POST (19 - WG_G - 4*WG_B)        // post-store, pre c.j   [S:1130]
// SE0 width: nx6p3delay n at S:1086 -> n = (SE0_TARGET - 5 - WG_G)/(2 + WG_B)
#define WGDELAY_SE0_N       ((48 - 5 - WG_G)/(2 + WG_B)) // match-V003 ~48cyc; use 64 for 2-bit nominal (?)
#define WGDELAY_TURNAROUND  WG_CLAMP0(7 - 2*WG_G)        // post-acquire, pre-preamble [S:836]
```

Invariant to preserve when applying: TX zero-path **store index stays 7+B** — pad only after the
store in send_zero_bit; pretok store index target = 7 (pad the taken path up, not the flip path
down); stuffed store index target = 39+B.

## E. Sites where the WG015 sequence differs in length from V003

| Site | WG015 | V003 | Δ (insns) | Δ (cycles, @(1,1)) |
|---|---|---|---|---|
| TX bus acquire (usb_send_data head) S:828–840 vs S:842–862 | li,sw MASKLB,li,sw OUTENSET,li t1 = 6 insns | lw CFGLR,li(2),and,li(2),or,li(2),sw BSHR,sw CFGLR,li t1(2) = 13 insns | **−7** | −7+2G ⇒ −5 |
| TX bus release S:1082–1093 vs S:1097–1112 | li,sw ; nx(7) ; li,sw ; li,sw OUTENCLR | li(2),sw ; nx(7) ; li(2),sw ; lw,li(2),and,li(2),or,sw | **−8** (−2 asserts, −6 release) | ≈ −8+3G ⇒ −5; J-hold 2+G vs ≈8 |
| PLIC ack (interrupt_complete) S:625–638 vs S:639–648 | la GPIO,li,sw INTSTATUS,la PLIC,lw claim,sw complete = 8 insns | la EXTI,li,sw INTFR = 4 insns | **+4** | +4+G+2P ⇒ +7 (ISR tail only, not bit-critical; delays IRQ re-arm) |
| SE0 keepalive timebase S:741–744 vs S:746–749 | la,lw,csrr(2) = 3 insns/4 cyc | la,la,lw,lw = 4 insns | −1 | ≈0 (csrr drains) |
| Trim actuator S:778–794 | absent | 9 insns | −9 | keepalive path only |
| nx6p3delay | (2+B)n+3 | 6n+3 | — | n=2: 9 vs 15; n=3: 12 vs 21; n=7: 26 vs 45 |
| XW_C_* ×5 (S:482/520/528/729/980) | 32-bit lbu/lhu/sb, 1 cyc | 16-bit XW, 1 cyc | 0 cyc | fetch footprint +2 bytes each; S:980 is in the counted TX loop (ideal-fetch: no change) (?) |
| DEBUG_TICK_MARK (per marked slot) | 1+G | 1 | 0 | +G inside every RX slot count above (already folded in) |
| Edge-catcher slot | 3+G | 4 (V003 effective) | — | exact at G=1 |

## F. Open uncertainties (all marked (?) above)

1. Unconditional j/c.j/jr = 1+B assumed (12+ occurrences in critical paths); if jumps are free
   even at B=1, every 4B/3B/2B coefficient drops by the jump count.
2. GPIO **sw** may be posted (cheaper than lw): all TX slot formulas would lose G; store-index
   formulas unchanged (issue slot). Bench with back-to-back MASKLB stores.
3. `la` expansion = 2 (relaxation may shrink `la t0,0x80` S:262 and locals to 1).
4. Budget "32 per preamble_loop iteration" assumes 1 iter = 1 preamble bit.
5. mret / trap-entry latency excluded (T_irq, T_mret) — affects A1/A2 phase and IRQ re-arm only.
6. V003 cycle columns are instruction counts, not measured cycles; V003 SE0 width ≈48 inferred
   from 6n+3, not from an LA capture.
7. `lbu` in load_next_byte (S:980) and `sb` in HANDLE_EOB (S:335) assumed TCM (1 cyc); flash- or
   contended-TCM-resident buffers break this (placement rule, see redteam findings).
