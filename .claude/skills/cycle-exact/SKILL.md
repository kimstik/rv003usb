---
name: cycle-exact
description: Methodology for cycle-exact and resource-constrained engineering — bit-banged protocols, timing-critical assembly, MCU firmware fighting a cycle or byte budget, and any "it doesn't fit" problem. Covers how to find hidden resources that look absent, how to verify claims against the built artifact instead of the source, and how to orchestrate long agent runs so failures cost nothing. Use this whenever the work involves a fixed cycle budget per operation, a hard timing deadline, an ISR that must not overrun, hand-written assembly whose instruction count matters, a firmware image that must fit a specific part, or someone concludes that a target cannot be met. Especially use it when a budget analysis says something is impossible — that conclusion is usually premature and this skill says where to look.
---

# Cycle-exact engineering

Work where a budget is fixed by physics and the question is what fits inside it: a bit
cell that lasts exactly N cycles, an image that must fit a part, a response that must
start before a deadline. The budget does not negotiate, so the leverage is entirely in
knowing what is actually spent and what is actually needed.

Two failure modes dominate this work, and they are opposites. One is believing a claim
that was never measured. The other is believing a limit that was never tested. Most of
what follows is defence against those two.

## Finding resource that looks absent

When a budget does not close, the instinct is to shave the work. Before that, look for
work that should not be there at all. Two patterns account for most of it, and both hide
in code that has already been read carefully — including by you.

### Computation whose result nobody reads

Trace every value the hot path maintains to every path that consumes it. A value computed
on a path where no consumer exists is free money, and it survives review because reviewers
check that code is *correct*, not that it is *reachable in purpose*.

The tell is a shared routine serving several cases: it computes the union of what all
cases need, so each individual case carries the others' work.

> A USB receive engine folded CRC16 into its bit cell for every packet. Tokens are
> protected by CRC5 and the token path never read the CRC16 residue — yet the table
> lookup and an entire pipeline stage ran anyway. Deleting them freed 17 cycles, which
> was more than the feature being added cost, so the "harder" direction came out faster
> than the one already built.

Build the table: value → every path that reads it. Write it out even where it finds
nothing; the table is what makes the search a method rather than a hope.

### Content that carries no information

Protocols are full of fixed patterns: preambles, sync words, padding, framing. Anything
whose content is known in advance can be emitted or processed **before** the thing that
decides it is known. This converts a serial dependency into an overlap, and the gain is
the full duration of the pattern.

The safety argument must be explicit: the *committing* part — the field that actually
decides — stays downstream of the verdict. Then a wrong speculation is structurally
impossible rather than merely unlikely.

> A device had to respond within 6.5 bit times and needed 9. But the response begins
> with SYNC: eight bit times of a fixed pattern carrying nothing. Emitting SYNC before
> the CRC verdict existed, and keeping the PID — which is what actually says "accepted" —
> downstream of the check, moved 128 cycles off the deadline and made the target
> reachable. A false accept remained impossible because the PID never preceded the check.

### Where else to look, in rough order of yield

- **Work on the deadline that does not depend on the last input.** Move it earlier or later.
- **Redundancy already in the format.** If a field is a checksum, complement, or duplicate
  of another, it may validate something else for free.
- **Pipeline depth.** A pipeline that runs N stages behind pays N stages of flush at the
  end. That flush is often the largest single item in a latency budget, and the depth is
  usually chosen for throughput without anyone costing the flush.
- **Precompute at build time.** Anything that is a pure function of constants — descriptor
  tables, encoded frames, stuffed bit streams — can be emitted by a generator instead of
  computed at run time.
- **Padding.** Idle cycles inserted to hit an exact count are budget, not waste — but check
  which build you measured. Padding differs wildly between variants of the same engine.

## Verification, and why the obvious methods fail here

### Measure the built artifact, not the source

Assembly and cycle counts must be checked on the object or the linked image. Source reads
correctly and assembles into something else more often than seems possible: a
line-continuation backslash inside a comment silently swallowing an instruction through
preprocessor line splicing; a fall-through where a branch was intended; an assembler
choosing a different encoding.

A control-flow-blind tool that reports a range per straight-line block is genuinely useful
— it catches mis-costed and forgotten instructions — but a path that branches out and back
must be traced by hand in the disassembly. State that limitation wherever the tool's
output is quoted, or someone will read a block total as a path total.

### A model that shares a source with the artifact cannot validate it

If a checker builds its reference from the same generator the artifact was built from,
agreement proves nothing about the artifact.

> A hand-written table row had all sixteen entries wrong. A bit-exact model passed 433
> packets against it, because the model built its own table from the generator instead of
> reading the assembled object. Only a direct object-versus-generator diff caught it.

So: diff embedded tables against their generator, from the binary. And document tables *as*
their generator rather than as data — it is shorter, unambiguous, and it makes the
authority explicit.

### At least one check must execute the linked image

"Read the object, not the source" is not far enough. A checker can read the assembled
object and still miss anything that only exists after linking — where a shared symbol
landed, what a cross-file reference resolved to, whether a weak symbol got a definition.

> A shared lookup table was moved 32 bytes to make room in front of it. A second consumer
> in a different file indexed it from the old base. Both files assembled, both cycle
> budgets held, and three separate bit-exact models passed — every one of them read the
> table from the *source*, so every one of them was consistent with itself and with a
> broken image. The one check that executed the linked image on an emulator threw on the
> first packet.

Two habits follow. Make cross-file offsets the **linker's** problem — export the symbol
the other file needs (`.set base, label - 32`, `.global base`) instead of writing the
number in two places; a comment saying "these must agree" is a defect waiting for its
turn. And keep one check in the suite that runs the actual image, however slow: on a
budget of many fast source-level models plus one slow executing one, the slow one is the
only one that can fail for a reason the others structurally cannot see.

Corollary, learned the same day: a verification that has never been *run* on the branch it
is committed to does not exist. The executing check above had a missing import — its
renderer module was never committed — so it had failed instantly since the day it was
written, and the two defects hid each other. Run every checker from a clean checkout
before believing any of them.

### Before believing "unmeasured", look

A gap in your knowledge is not a gap in the record. Check the source document, run the
assembler, install the toolchain.

> A cost figure was declared "the single number that decides this, and it is unmeasured".
> It was in the same table as everything else, one row down. A conclusion had already been
> built on its absence.

The same applies to capability. Do not assert that a resource is unavailable — network,
tool, source repository — without testing it in the current environment. That mistake
cost this project a day of re-deriving what two `git clone`s would have supplied.

### Numbers carry their configuration

A cycle count is meaningless without: which build, which cost column, which execution
location, which variant. The same engine measured 6 % padding in one build and 44 % in
another, and a recommendation was made from the wrong one.

When costs depend on where code executes, expect the columns to **swap** rather than
scale, and re-derive rather than adjusting.

## When the target is declared unreachable

"No solution found" is not "no solution exists". Before accepting a negative result:

1. **Re-read your own documents.** A design that closes the gap may already be written
   down, from an earlier pass, and forgotten. This happens more than anyone expects.
2. **Check whether a blocking number was measured or assumed.** Estimates of unwritten
   code are the usual culprit, and they are usually pessimistic because they cost the
   general case when the specific case needs far less.
3. **Check the arithmetic for double-counting.** A budget that already includes an
   optimisation cannot be improved by that optimisation again.
4. **Ask what the deadline is measured from.** Zero points move, and the difference is
   often larger than the shortfall.

> An implementation's arm cost was estimated at 30 cycles and used to declare a target
> unreachable. Built, it was 13 — the 30 had priced a general entry needing state the
> actual path never touches.

## Running this work through agents

Sessions doing this work are long, and agents die: usage limits, output ceilings, context
exhaustion. The arrangement that makes deaths free:

- **One isolated worktree per agent, disjoint file ownership.** Files survive in the
  worktree whether or not the agent finishes. Across many deaths in one project this cost
  one lost document.
- **Commit after each piece, and write in pieces.** One agent was lost entirely by trying
  to emit a large file in a single response and exceeding the output ceiling before its
  first commit. "Commit often" is not enough advice on its own.
- **Order the work by value.** Tell the agent to produce the highest-value section first,
  so a truncated run still yields the usable part.
- **Re-verify what agents report.** Not from distrust — their claims are usually right in
  substance and wrong in a number, and the wrong number is often traceable to a tool *you*
  gave them. Check the headline claim yourself before acting on it.
- **Give findings, not just tasks.** An agent that knows what has already been established
  spends its run extending the frontier instead of rediscovering it.

## Reporting

State what was measured, how, and under which configuration. Separate verified from
reasoned. When a previous conclusion is overturned — including your own — say so plainly
and say what the error was, because the reasoning that produced it will otherwise be
reused.

A negative result with arithmetic is a real deliverable. "This direction dies here, for
this reason" prevents the next person from spending a run on it.
