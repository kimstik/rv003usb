/* engine16_balance.S -- BALANCE entrant, Engine-16 competition (PY32 / Cortex-M0+)
 *
 * USB low-speed (1.5 Mbit/s) receive engine, 16 cycles per bit cell at
 * 24 MHz / Flash Latency 0.  See engine16_balance.md for the design note,
 * lineage and the per-path cycle ledger with arithmetic shown -- this file
 * is deliberately light on prose comments; the .md is the source of truth
 * for *why*, this file is the source of truth for *what actually assembles*.
 *
 * Assembles standalone with:
 *   arm-none-eabi-gcc -x assembler-with-cpp -mcpu=cortex-m0plus -mthumb -c \
 *       engine16_balance.S -o engine16_balance.o
 *
 * Register/pin conventions and buffer layout are local to this file (spec
 * ENGINE16_SPEC.md S4: "internal labels ... are all free").
 */
.syntax unified
.thumb
.cpu cortex-m0plus

/* ---- configuration (free choices, stated so they can be checked) -------- */
.equ PIN_DM,        6          /* D- bit position in GPIOx_IDR */
.equ PIN_DP,        7          /* D+ bit position in GPIOx_IDR, adjacent to D- */
.equ DMASK2,        (1<<PIN_DM)|(1<<PIN_DP)   /* 0xC0 -- fits an 8-bit movs immediate */
.equ GPIOB_BASE,    0x50000400
.equ IDR_OFFSET,    0x10
.equ BUFSIZE,       16         /* power of two -- structural buffer bound, see .md D-2 */
.equ BUFMASK,       BUFSIZE-1

/* ---- register roles in the hot loop (all low registers -- see .md S5) --- */
/* r0 GPIO_ADDR  persistent  GPIOB_BASE+IDR_OFFSET, constant for the ISR     */
/* r1 DMASK2     persistent  0xC0, constant for the ISR                     */
/* r2 PREV       persistent  previous D- level, sign-extended mask          */
/* r3 SHIFT_BUF  persistent  byte accumulator (inverted sense, fixed at     */
/*                           the byte boundary -- see .md S2)               */
/* r4 ONESRUN    persistent  consecutive-decoded-1s counter, 0..6           */
/* r5 BITCOUNT   persistent  bits remaining in the current byte, 8..1       */
/* r6 scratch    transient                                                  */
/* r7 scratch    transient                                                  */
/* r8 BUFPTR (mov-tax) persistent  byte index into rxbuf, touched only at   */
/*                     the byte boundary -- see .md S5                     */

.pushsection .bss.rxbuf,"aw",%nobits
.balign 4
rxbuf: .space BUFSIZE
.popsection

.pushsection .datacode,"ax",%progbits
.thumb_func
.type engine16_balance_entry, %function
engine16_balance_entry:
    /* Untimed setup: constants, zeroed state.  SYNC/phase lock is sketched,
     * not implemented -- see .md "what I gave up".  Falls into bit_cell once
     * locked. */
    ldr   r0, =(GPIOB_BASE + IDR_OFFSET)
    movs  r1, #DMASK2
    movs  r2, #0
    movs  r3, #0
    movs  r4, #0
    movs  r5, #8
    movs  r6, #0
    mov   r8, r6                 /* BUFPTR = 0 */

/* =========================================================================
 * bit_cell -- the timed loop body.  See engine16_balance.md S3 for the
 * cycle ledger on every path (data 1, data 0, stuffed bit, byte boundary,
 * SE0), arithmetic shown.
 * ========================================================================= */
.balign 4
bit_cell:
    ldr   r6, [r0]               /* sample GPIO IDR                        */
    ands  r6, r1                 /* r6 = raw & DMASK2; Z set iff SE0       */
    beq   se0_bit                /* not taken -- common case               */
    lsls  r6, r6, #(31-PIN_DM)   /* isolate D- -> bit31                    */
    asrs  r6, r6, #31            /* r6 = newLevel(D-), 0x0/0xFFFFFFFF      */
    eors  r6, r2                 /* r6 = transitionMask (r2 untouched)     */
    eors  r2, r6                 /* r2 = newLevel -> PREV updated          */
    cmp   r4, #6
    beq   stuff_bit               /* not taken -- common case, <=6/7        */
    adds  r4, #1                  /* tentative ones-run increment          */
    bics  r4, r6                  /* finalize: 0 if transition else r4+1   */
    lsrs  r6, r6, #31             /* r6 = 0/1; Carry = transitionMask's bit */
    adcs  r3, r3                  /* SHIFT_BUF = (SHIFT_BUF<<1)|carry       */
    subs  r5, #1                  /* BITCOUNT--                             */
    beq   byte_boundary           /* not taken -- common case, <=7/8        */
    b     bit_cell                /* loop back -- ambiguous 2-3, see .md    */

/* ---- stuffed bit: discard, reset the ones-run only ---------------------- */
.balign 4
stuff_bit:
    movs  r4, #0
    nop                           /* pad: makes the worst-case (both taken
                                     branches cost 3) total exactly 16; see
                                     .md S3 -- best case then finishes 2
                                     cycles early, which spec S1 allows */
    b     bit_cell

/* ---- byte boundary: fix polarity, store, advance+mask BUFPTR, reset ----- */
.balign 4
byte_boundary:
    mvns  r3, r3                 /* fix inverted sense (deferred per-byte) */
    ldr   r6, =rxbuf             /* literal pool: RAM address, RAM code=4  */
    mov   r7, r8                 /* r7 = BUFPTR                            */
    strb  r3, [r6, r7]           /* store byte                             */
    adds  r7, #1
    movs  r6, #BUFMASK
    ands  r7, r6                 /* structural wrap -- D-2, see .md        */
    mov   r8, r7
    movs  r5, #8                 /* BITCOUNT reset                         */
    movs  r3, #0                 /* SHIFT_BUF reset                        */
    b     bit_cell

/* ---- SE0 / EOP: terminal for this packet, not a repeating bit cell ------ */
.balign 4
se0_bit:
    ldr   r6, =se0_bit_flash
    bx    r6
.popsection

.thumb_func
se0_bit_flash:
    /* Untimed tail: CRC5/CRC16 over rxbuf[0 .. BUFPTR), PID dispatch, call
     * into the existing C layer.  Sketched in engine16_balance.md S4, not
     * implemented here -- spec S4: "the C layer ... must not change". */
    bx    lr
