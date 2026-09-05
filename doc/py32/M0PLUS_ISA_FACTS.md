# Cortex-M0+ instruction availability — checked with the assembler, not recalled

Every line below was verified by assembling it with the installed toolchain:

```
arm-none-eabi-as -mcpu=cortex-m0plus -mthumb
```

This file exists because the commonest way an agent-written `.S` fails is by
using an instruction that exists on Cortex-M3/M4 but not on baseline ARMv6-M.
Check here before relying on anything; if it is not listed, assemble it and find
out rather than assuming either way.

## Available

| instruction | note |
|---|---|
| `rev`, `rev16` | byte reversal — present, unlike the rest of the bit-manipulation family |
| `adcs`, `sbcs` | carry-chain arithmetic; usable to fold a flag into a value without branching |
| `rors` | register rotate (flag-setting form) |
| `muls rd, rn, rd` | 32x32 low multiply; **cycle cost is implementation-defined on M0+** — verify before putting one in a bit cell |
| `ldm rn!, {…}` / `stm rn!, {…}` | burst transfer; from RAM-resident code 2 cycles for the first register and +1 each after, so 4 words cost 5 cycles rather than 8 |
| `ldr/str/ldrb/ldrh rd, [rn, rm]` | **register-offset addressing** — makes table-driven decode possible |
| `mov pc, rn`, `bx rn` | computed branch / jump table, 3 cycles |
| `uxtb`, `sxth` | zero/sign extension |
| `lsls`, `lsrs`, `asrs`, `eors`, `ands`, `bics`, `mvns`, `rsbs`, `cmn`, `tst` | the ordinary flag-setting data-processing set |

## Not available

| instruction | consequence |
|---|---|
| **`it` / IT blocks** | **there is no predication on M0+.** "Branchless" here cannot mean conditional execution — it must mean arithmetic on masks. The idiom the existing engine already uses is `lsls rX, rY, #31` then `asrs rX, #31` to turn a bit into an all-ones or all-zeros mask, then `ands`. This is the single most important entry in this file. |
| `rbit` | no bit reversal; `rev` reverses bytes only |
| `clz` | no count-leading-zeros, so no cheap "find the first transition" |

## Why the absence of `it` dominates the design

Every competitor is under pressure to remove taken branches, because a taken
branch costs 2-3 cycles and the ambiguity depends on alignment
(`ENGINE16_SPEC.md` §2) — 6 % of a bit cell at this budget. On Cortex-M3/M4 the
natural answer is an IT block. That answer does not exist here.

What remains is mask arithmetic: produce `0x00000000` or `0xFFFFFFFF` from the
sampled bit, then `ands`/`eors`/`bics` the two candidate results together. That
costs roughly two extra instructions per decision but is perfectly constant-time
and immune to the 2-vs-3 ambiguity. Whether that trade wins at 16 cycles is the
question each design has to answer with its own arithmetic.

A caution against the opposite error: this is not a reason to make everything
branchless. A branch that is *never taken* on the common path costs 1 cycle,
which is cheaper than any mask sequence. The right shape is usually to arrange
the common case as fall-through and pay 2-3 only on the rare path.

## Reproducing

```
echo '.syntax unified
.thumb
.cpu cortex-m0plus
<instruction>' > t.s && arm-none-eabi-as -mcpu=cortex-m0plus -mthumb t.s -o t.o
```

Note the syntax trap that produced two false negatives while this file was being
made: `ror r0, r1` and `mul r0, r1` fail to assemble, which looks like the
instructions are absent. They are not — the flag-setting forms `rors` and
`muls rd, rn, rd` assemble fine. A failed assembly means "not in that form",
which is not the same as "not on this core", and the difference matters.
