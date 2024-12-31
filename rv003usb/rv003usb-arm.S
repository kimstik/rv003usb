#include "rv003usb.h"

#define EXTI  0x40021800
#define GPIOA 0x50000000
#define GPIOB 0x50000400
#define GPIOC 0x50000800
#define GPIOF 0x50001400

#define GPIO_MODER_MODE0_Pos           (0U)
#define GPIO_MODER_MODE0_Msk           (0x3UL << GPIO_MODER_MODE0_Pos)          /*!< 0x00000003 */
#define GPIO_MODER_MODE0_OUTPUT        (0x1UL << GPIO_MODER_MODE0_Pos)          /*!< 0x00000001 */

#define MODER_OFFSET 0x0
#define IDR_OFFSET  0x10
#define BSRR_OFFSET 0x18

#define EXTI_PR_OFFSET 0x0C

#define LOCAL_CONCAT(A, B) A##B
#define LOCAL_EXP(A, B) LOCAL_CONCAT(A,B)

BITCOUNT  .req r1 // usb_send_data(..., uint32_t length, ...) * 8
SHIFT_BUF .req r3
SCRATCH   .req r4
BITSTUFF  .req r6
CRC       .req r7
GPIO_BASE .req r9


.pushsection .bss.rxbuf,"aw",%nobits
.balign 4
rxbuf: .space 3 + USB_BUFFER_SIZE
.popsection


.global	LOCAL_EXP(USB_DM_IRQ, Handler)
.pushsection .datacode,"ax",%progbits
.thumb_func
.type LOCAL_EXP(USB_DM_IRQ, Handler), %function
LOCAL_EXP(USB_DM_IRQ, Handler):
	ldr r3, =LOCAL_EXP(GPIO, USB_PORT)
	ldr r0, [r3, #IDR_OFFSET]

	push {r4, r5, r6, r7, r14}
	mov	r14, r9
	mov	r7, r8
	push {r7, r14}

	ldr r2, =USB_DMASK
	and r0, r2
	beq handle_se0_keepalive // SE0?

	mov GPIO_BASE, r3

	mov BITCOUNT, #8
	mov BITSTUFF, #6

#define DELAY_CYCLES(cycles) mov SCRATCH, #((cycles+1) / 3); sub SCRATCH, #1; bne .-1

	// 90 to 117 would work assuming no clock deviation,
	// use less than the mean so it'll work with a delayed interrupt
	DELAY_CYCLES(96)
	// TODO: keep code up to here in flash to conserve RAM

	//      |<------------------- sync pattern -------------------->|
	// _____        ______        ______        ______               ______ _
	// idle \______/      \______/      \______/      \_____________/_PID0_X_
	//                                   ^
	//                              we are here, wait for rising edge
preamble_loop:
	ldr r5, [r3, #IDR_OFFSET]
	and r5, r2
	cmp r5, r0
	beq preamble_loop

	cmp r5, #0
	beq done_usb_message // SE0?

	mov r12, r2	// usb pin mask
	ldr r2, =rxbuf + 3
	mov SHIFT_BUF, #0

	DELAY_CYCLES(71)

packet_type_loop:
	DELAY_CYCLES(12)          // 11
	nop                       // 23

	// Up here to delay loop a tad, and we need to execute them anyway.
	ldr CRC, =0xffff          // 24
POLY_RX .req r14
	ldr SCRATCH, =0xa001      // 26
	mov POLY_RX, SCRATCH      // 28

	lsl SHIFT_BUF, #1         // 29

	mov r5, GPIO_BASE         // 30
	mov SCRATCH, r12          // 31
	ldr r5, [r5, #IDR_OFFSET] //  0
	and r5, SCRATCH           //  1
	eor r5, r0                //  2
	beq pt_got_one            //  3
	mov BITSTUFF, #6          //  4
	eor r0, r5                //  5
	bne pt_got_zero           //  6
	b done_usb_message // SE0 would be invalid here, check anyway
pt_got_one:
	mov SCRATCH, #1           //  5
	orr SHIFT_BUF, SCRATCH    //  6
	sub BITSTUFF, #1          //  7
pt_got_zero:

	sub BITCOUNT, #1          //  8
	bne packet_type_loop      //  9

///////////////////////////////////////////////////////////////////////////////////////
///////////////////////////////////////////////////////////////////////////////////////	

	// XXX Here, figure out CRC polynomial.

	// WARNING: SHIFT_BUF is bit-wise backwards here.
	// 0xb4 for instance is a setup packet.
	//
	// When we get here, packet type is loaded in SHIFT_BUF.
	// If packet type is 0xXX01 or 0xXX11
	// the LSBs are the inverted packet type.
	// we can branch off of bit 2.
	mov r5, #0x14             // 10
	mov SCRATCH, #0x0c        // 11
	and SCRATCH, SHIFT_BUF    // 12

	// if SCRATCH is 1 then it's DATA (full CRC) otherwise,
	// (0) for setup or PARTIAL CRC.
	// Careful:  This has to take a constant amount of time either way the branch goes.
	beq .+4 // skip mov       // 13
	mov POLY_RX, r5           // 14
	beq .+4 // skip mov       // 15
	mov CRC, #0x1e            // 16

	b .+2                     // 17

///////////////////////////////////////////////////////////////////////////////////////
///////////////////////////////////////////////////////////////////////////////////////	
is_end_of_byte:
	// TODO: prevent buffer overrun
	mov BITCOUNT, #8          // 19
	strb SHIFT_BUF, [r2]      // 20
	add r2, #1                // 22

bit_process:
	DELAY_CYCLES(6)           // 23

	lsr SHIFT_BUF, #1         // 29

	mov r5, GPIO_BASE         // 30
	mov SCRATCH, r12          // 31
	ldr r5, [r5, #IDR_OFFSET] //  0
	and r5, SCRATCH           //  1

	eor r5, r0                //  2

	beq pl_got_one            //  3
	eor r0, r5                //  4
	beq se0_complete          //  5
	mov BITSTUFF, #6          //  6

	// Handle CRC (0 bit)  (From @Domkeykong)
	lsl SCRATCH, CRC, #31     //  7
	asr SCRATCH, #31          //  8
	mov r5, POLY_RX           //  9
	and r5, SCRATCH           // 10

	nop                       // 11
	b pl_got_zero             // 12
pl_got_one:
	mov SCRATCH, #0x80        //  5
	orr SHIFT_BUF, SCRATCH    //  6

	//HANDLE_CRC (1 bit)
	mov SCRATCH, #1           //  7
	and SCRATCH, CRC          //  8
	sub SCRATCH, #1           //  9
	mov r5, POLY_RX           // 10
	and r5, SCRATCH           // 11

	sub BITSTUFF, #1          // 12
	beq rx_stuffed            // 13
after_rx_stuffed:
pl_got_zero:

	lsr CRC, CRC, #1          // 14
	eor CRC, r5               // 15

	sub BITCOUNT, #1          // 16
	beq is_end_of_byte        // 17
	b .+2                     // 18
	nop                       // 20
	b bit_process             // 21

rx_stuffed:
	// TODO: check for SE0?, maybe try to re-sync
	DELAY_CYCLES(24)          // 15
	b .+2                     //  7

	mov BITSTUFF, #6          //  9
	mov SCRATCH, r12          // 10
	eor r0, SCRATCH           // 11

	b after_rx_stuffed        // 12

	// rest of interrupt handler is not time-critical,
	// continue in flash to conserve RAM.
se0_complete:
	ldr r0, =se0_complete_flash
	bx r0

handle_se0_keepalive:
	// TODO
	ldr r0, =interrupt_complete
	bx r0

done_usb_message:
	ldr r0, =done_usb_message_in
	bx r0
.popsection 

.thumb_func
se0_complete_flash:
	// This is triggered when we finished getting a packet.
	sub BITCOUNT, #8 // Make sure we received the last byte fully.
	bne done_usb_message_in

	// Special: handle ACKs?
	// Now we have to decide what we're doing based on the
	// packet type.
	ldr r1, =rxbuf + 3
PID .req r4
	ldrb PID, [r1]
	add r1, #1

	// 0010 => 01001011 => ACK
	// 0011 => 11000011 => DATA0
	// 1011 => 11010010 => DATA1
	// 1001 => 10010110 => PID IN
	// 0001 => 10000111 => PID_OUT
	// 1101 => 10110100 => SETUP    (OK)

	// For ACK don't worry about CRC.
	sub PID, #0b01001011

	ldr r5, =rv003usb_internal_data

	// ACK doesn't need good CRC.
	bne not_ack
	//void usb_pid_handle_ack( uint32_t dummy0, uint32_t dummy1, uint32_t dummy2, uint32_t dummy2, struct rv003usb_internal * ist );
	push {r5}
	ldr r0, =usb_pid_handle_ack
	blx r0
	add sp, #4
	b done_usb_message_in

not_ack:
	// Next, check for tokens.
	cmp CRC, #0
	bne crc_for_tokens_would_be_bad_maybe_data
may_be_a_token:
	// Our CRC is 0, so we might be a token.

	// Do token-y things.
	ldrh r3, [r1]
	mov r0, #0x7f // addr
	and r0, r3
	lsr r3, #7
	mov r2, #0xf  // endp
	and r2, r3
	cmp r2, #ENDPOINTS
	bhi done_usb_message_in // Make sure < ENDPOINTS
	cmp r0, #0
	beq yes_check_tokens
	// Otherwise, we might have our assigned address.
	ldrb r3, [r5, #MY_ADDRESS_OFFSET_BYTES]
	cmp r0, r3
	bne done_usb_message_in // addr != 0 && addr != ours.
yes_check_tokens:
	add PID, #(0b01001011-0b10000111)
	bne not_out
	//void usb_pid_handle_out( uint32_t dummy0, uint32_t dummy1, uint32_t endp, uint32_t dummy3, struct rv003usb_internal * ist );
	ldr r0, =usb_pid_handle_out
	b call_token_handler
not_out:
	add PID, #(0b10000111-0b10010110)
	bne not_in
	//void usb_pid_handle_in( uint32_t dummy0, uint8_t * data, uint32_t endp, uint32_t dummy3, struct rv003usb_internal * ist );
	ldr r0, =usb_pid_handle_in
	b call_token_handler
not_in:
	add PID, #(0b10010110-0b10110100)
	bne done_usb_message_in
	//void usb_pid_handle_setup( uint32_t dummy0, uint32_t dummy1, uint32_t endp, uint32_t dummy3, struct rv003usb_internal * ist );
	ldr r0, =usb_pid_handle_setup
call_token_handler:
	push {r5}
	blx r0
	add sp, #4
	b done_usb_message_in

	// CRC is nonzero. (Good for Data packets)
crc_for_tokens_would_be_bad_maybe_data:
	ldr r0, =0xb001
	cmp CRC, r0
	bne done_usb_message_in
	// Good CRC!!
	sub r3, r2, r1
	add r3, #1

	//void usb_pid_handle_data( uint32_t dummy0, uint8_t * data, uint32_t which_data, uint32_t length, struct rv003usb_internal * ist );
	ldr r0, =usb_pid_handle_data
	mov r2, #0
	add PID, #(0b01001011-0b11000011)
	beq data0
	add PID, #(0b11000011-0b11010010)
	bne done_usb_message_in
	mov r2, #1
data0:
	push {r5}
	blx r0
	add sp, #4

.thumb_func
done_usb_message_in:
.thumb_func
interrupt_complete:
	// Acknowledge interrupt.
	ldr r0, =EXTI
	mov r1, #(1 << USB_PIN_DM)
	str r1, [r0, #EXTI_PR_OFFSET]

	// Restore stack.
	pop	{r6, r7}
	mov	r9, r7
	mov	r8, r6
	pop	{r4, r5, r6, r7, pc}


.global usb_send_empty
//void usb_send_empty( uint32_t token );
usb_send_empty:
	mov r1, #2
	mov r2, #2
	mov r3, r0
	ldr r0, =always0

.balign 4
.global usb_send_data
//void usb_send_data( const void * data, uint32_t length, uint32_t poly_function, uint32_t token );
usb_send_data:
	push {r4, r5, r6, r7, r14}
	mov	r14, r9
	mov	r7, r8
	push {r7, r14}

	// ASAP: Turn the bus around and send our preamble + token.
	ldr r6, =LOCAL_EXP(GPIO, USB_PORT)
	ldr r5, =(1<<USB_PIN_DP) | (1<<(USB_PIN_DM+16))
	str r5, [r6, #BSRR_OFFSET]

	// prepare value for MODER register
	ldr r7, [r6, #MODER_OFFSET]
	ldr SCRATCH, =~((GPIO_MODER_MODE0_Msk << (USB_PIN_DP*2)) | (GPIO_MODER_MODE0_Msk << (USB_PIN_DM*2)))
	and r7, SCRATCH
	ldr SCRATCH, =(GPIO_MODER_MODE0_OUTPUT << (USB_PIN_DP*2)) | (GPIO_MODER_MODE0_OUTPUT << (USB_PIN_DM*2))
	orr SCRATCH, r7

	ldr r7, =0xa001
POLY_TX .req r2 // usb_send_data(..., uint32_t poly_function, ...)
	//poly_function = 0 to include CRC.
	//poly_function = 2 to exclude CRC.
	lsr POLY_TX, #1
	sub POLY_TX, #1
	and POLY_TX, r7

	nop
	// set push-pull mode
	str SCRATCH, [r6, #MODER_OFFSET]
	mov GPIO_BASE, r6

	ldr SCRATCH, =(1<<USB_PIN_DP) | (1<<USB_PIN_DM) | (1<<(USB_PIN_DP+16)) | (1<<(USB_PIN_DM+16))
FLIP_MASK .req r8
	mov FLIP_MASK, SCRATCH

	// Save off our preamble and token.
	lsl r3, #7 //Put token further up so it gets sent later.
	mov SCRATCH, #0x40
	orr SHIFT_BUF, r3, SCRATCH

	// r1 has length in bytes, convert to length in bits in r12
	lsl r1, #3
	mov r12, r1

	mov BITCOUNT, #15 // 15 bits.

#if PY32F002Bx5
	b .+2
.balign 4
#else
	nop
	nop
#endif
////////////////////////////////////////////////////////////////////////////
// Send preamble + token
pre_and_tok_send_inner_loop:
	sub BITSTUFF, #1

	lsr SHIFT_BUF, #1	// really lsrs
#if PY32F002Bx5
	bcs pre_and_tok_delay_one_bit
	mov SCRATCH, FLIP_MASK
#else
	mov SCRATCH, FLIP_MASK
	bcs pre_and_tok_send_one_bit
.ifeq (pre_and_tok_send_inner_loop - usb_send_data) & 2
	.error "pre_and_tok_send_inner_loop must be unaligned"
.endif
#endif

	eor r5, SCRATCH

	mov BITSTUFF, #6 // reset bit stuffing.
pre_and_tok_send_one_bit:
	sub BITCOUNT, #1 // really subs
	mov SCRATCH, GPIO_BASE
	str r5, [SCRATCH, #BSRR_OFFSET]
	beq pre_and_tok_done_sending_data

	// we have some more time to prepare stuff
	ldr CRC, =0xffff

	b .+2
	nop
	nop
	nop
	nop

#if PY32F002Bx5
	nop
#endif
	b pre_and_tok_send_inner_loop

.balign 4
pre_and_tok_delay_one_bit:
	b pre_and_tok_send_one_bit

.balign 4
pre_and_tok_done_sending_data:

	//Restore size.
	mov SCRATCH, r12
	cmp SCRATCH, #0 // TODO: movs?
	beq no_really_done_sending_data
	mov BITCOUNT, SCRATCH // TODO: load directly from r12

load_next_byte:
	ldrb SHIFT_BUF, [r0, #0]
	add r0, #1
send_inner_loop:
	lsl SCRATCH, CRC, #31
	asr SCRATCH, #31
	lsr CRC, CRC, #1

	lsr SHIFT_BUF, #1 // really lsrs
	bcs send_one_bit

	and SCRATCH, POLY_TX
	eor CRC, SCRATCH

flip_bus:
	mov SCRATCH, FLIP_MASK
	eor r5, SCRATCH

	mov SCRATCH, GPIO_BASE
	str r5, [SCRATCH, #BSRR_OFFSET]
	mov BITSTUFF, #6 // reset bit stuffing.

	sub BITCOUNT, #1 // really subs
	beq done_sending_data
	mov SCRATCH, #7
	tst BITCOUNT, SCRATCH
	beq load_next_byte
	b .+2
#if PY32F002Bx5
	nop
#endif
	b send_inner_loop

.balign 4
send_one_bit:
	mvn SCRATCH, SCRATCH
	and SCRATCH, POLY_TX
	eor CRC, SCRATCH

	sub BITSTUFF, #1 // really subs
	beq insert_stuffed_bit

send_end_bit_complete:
	sub BITCOUNT, #1 // really subs
	beq done_sending_data
	mov SCRATCH, #7
	tst BITCOUNT, SCRATCH
	beq load_next_byte
	b .+2
	nop
	b send_inner_loop

.balign 4
done_sending_data:
	// BUT WAIT!! MAYBE WE NEED TO CRC!
	cmp POLY_TX, #0
	beq no_really_done_sending_data
	mov BITCOUNT, #8 // Load 8 more bits out
	lsr POLY_TX, #8 // really lsrs
	// TODO: this branch should be taken with an additional delay
	beq send_inner_loop  //Second CRC byte
	// First CRC byte 
	mvn SHIFT_BUF, CRC
	b send_inner_loop

.balign 4
insert_stuffed_bit:
	b .+2; b .+2; b .+2; b .+2; b .+2
#if !PY32F002Bx5
	b .+2
#endif
	b flip_bus

.balign 4
no_really_done_sending_data:
	b .+2; b .+2; b .+2; b .+2
	nop

	// Need to perform an SE0.
	mov SCRATCH, FLIP_MASK
	lsl SCRATCH, SCRATCH, #16
	mov r0, GPIO_BASE
	str SCRATCH, [r0, #BSRR_OFFSET]

	b .+2; b .+2; b .+2; b .+2; b .+2; b .+2; b .+2; b .+2
	b .+2; b .+2; b .+2; b .+2; b .+2; b .+2; b .+2; b .+2
	b .+2

	mov SCRATCH, #1
	lsl SCRATCH, SCRATCH, #USB_PIN_DM
	str	SCRATCH, [r0, #BSRR_OFFSET]

	b .+2; b .+2; b .+2; b .+2; b .+2; b .+2

	// Convert D+/D- into inputs.
	// can now use any registers as scratch
	lsl	SCRATCH, SCRATCH, #USB_PIN_DM
	ldr	r1, [r0, #MODER_OFFSET]
	eor	r1, SCRATCH
	mov	SCRATCH, #1
	lsl	SCRATCH, #(USB_PIN_DP*2)
	eor	r1, SCRATCH
	str	r1, [r0, #MODER_OFFSET]

	pop	{r6, r7}
	mov	r9, r7
	mov	r8, r6
	pop	{r4, r5, r6, r7, pc}

.balign 4
always0:
	.word 0x00
