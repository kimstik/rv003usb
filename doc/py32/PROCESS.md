# When this work is done, and when it may not stop

Written because of a fair complaint: *every session opens with "I was wrong
about X" and closes with "what remains". That means the plan is not clear and
not organised.*

Half of that is a reporting failure and half is a real missing artifact. This
file is the missing artifact.

## 1. What the record actually shows

The open list never empties, which reads as a treadmill. It is not one — the
**severity of what is found falls monotonically**:

| date | found | severity |
|---|---|---|
| 09-08 | SYNC lock inverted — the receiver decodes **nothing** | fatal |
| 09-09 | the clock servo drives the clock to +25 % under traffic | fatal in operation |
| 09-09 | the device drives the bus with nothing owed, five shapes | serious |
| 09-09 | it ACKs a hub neighbour's DATA and flips that transfer's toggle | serious |
| 09-09 | GET_STATUS unanswered — Linux re-enumerates on every resume | moderate |
| 09-12 | 48 MHz dead band — 3 of 60 start points hunt | minor |
| 09-12 | coarse escape moves the wrong way — 8 of 383 | minor |
| open | D5, D6 | documentation |

A descent, not a circle. **It was never once presented that way**, which is
why it read as circling. Fixed by §4's rule: every session report states the
top open severity, and how it compares with the session's start.

## 2. Why every session opened with a correction

Every correction — without exception — was a claim about something **not yet
executed or measured**. Not one was a properly measured number later
overturned.

* entry window 6..37 — measured at one packet phase, reported as the window
* the ppm sign — taken from a docstring, not from watching the drift
* "decoded" — read the receive buffer, which is written *before* the CRC
  verdict, so it counted rejected frames as decoded
* "a host never sends a DATA without a token" — said before reading §11.8.2,
  which has a hub broadcasting to every low-speed port
* "fixing the coarse escape needs a divide" — accepted someone else's framing
  of the problem instead of asking what the servo actually requires

**Rule R1.** A number does not leave a session without the command that
produced it, named in the same sentence. A claim about what is *reachable* is
not made until something has executed it. If neither is available, the
sentence is "not measured" — which is a complete and acceptable answer.

## 3. The severity ladder

Progress is movement down this ladder, and nothing else. Instruments and
documents are not progress; they are what makes the next step down possible.

| level | meaning |
|---|---|
| **F** fatal | the stack cannot work at all: no packet decodes, no clock holds |
| **S** serious | it works alone but breaks a bus it shares, or loses a transfer |
| **M** moderate | it works, and a normal host hits a visible fault (a re-enumeration, a stall, a retry storm) |
| **m** minor | a corner reachable only near a band edge or in a non-default build |
| **d** doc | the code is right and something written about it is not |

## 4. The stop criterion — a session MAY end

All five, or it may not:

1. **Green.** Every instrument that exists runs and passes. Named, with its
   command, in the report.
2. **Traceable.** Every number stated this session has a reproducing command.
   (R1.)
3. **Landed.** Everything is committed and pushed. No finding lives only in
   chat.
4. **Corrected in the repo.** Anything found this session that invalidates a
   previously reported number is fixed *in the files*, not only acknowledged
   in conversation.
5. **Descended.** The top open severity is **strictly lower** than at session
   start, or the top item is unchanged *and the report says why it could not
   move* with the arithmetic.

## 5. The no-stop criterion — a session MUST NOT end

Any one of these forbids stopping, regardless of the turn budget:

1. An instrument is **red** and unexplained.
2. A claim was made this session with **no reproducing command** and it has
   not been withdrawn.
3. Something was found that **invalidates a previously reported number** and
   the repo still carries the old one.
4. **Uncommitted work** exists.
5. The top open severity is the **same as at session start** and no
   arithmetic says why. *This is the definition of circling.* If it fires
   twice in a row, the plan is wrong, not the work — stop executing and
   re-plan.

## 6. Done

Two levels, because one of them cannot be decided here and pretending
otherwise is how the finish line disappeared.

### Level A — decidable without hardware. This is a real finish line.

* no open item at severity **F**, **S** or **M**;
* every instrument green, each one named with its command;
* execution coverage of the linked image ≥ 90 % under the full enumeration,
  with every cold region named and justified (`COVERAGE.md`);
* every timed cell exactly 16 in all six `USB_RX_CHECK` ×
  `USB_ENGINE16_FLASH` builds, and the turnaround inside `[τ+60, τ+124]` at
  every EOP cell K;
* the bus invariant at **0 violations** over random traffic;
* the clock servo converges inside ±0.203 % from every start point within its
  stated capture range, and holds it under traffic;
* every number in every document carries the command that made it.

**When Level A holds, say so plainly and stop.** Not "what remains" — *done,
at Level A, and here is what Level B needs.*

### Level B — needs a board. Not decidable here, and not a reason to keep going here.

`PLAN.md` §10A's gates G0–G12. Level A is what makes a Level B session
interpretable: if something fails on a board while Level A holds, the board is
telling us something new.

## 7. The report format that makes §4 checkable

Every session ends with exactly this, and nothing else claiming to be a
summary:

```
severity at start:  <F|S|M|m|d>  <the item>
severity now:       <F|S|M|m|d>  <the item>
instruments:        <name: result, with command>  ...
corrected in repo:  <what previously-reported number changed, and where>
open, ranked:       <severity> <item> ...
stop criterion:     met / NOT met because <which of §5 fired>
```

If the two severity lines are equal and there is no arithmetic explaining it,
§5.5 has fired and the session does not end.
