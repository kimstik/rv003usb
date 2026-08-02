// dfu_rv003usb.h — TRANSPORT port of the DFU core onto the rv003usb bitbang
// stack.  Included at the BOTTOM of dfu.c (single TU); uses the core's
// dfu_class_request() and implements the statics it prototyped.
//
// A hardware-USB transport (e.g. the WG015 HW block on a fixed silicon
// revision, or any other chip's USB device IP) would be a sibling header
// implementing the same three statics + its stack's class-request callback.
//
// rv003usb specifics used here (all line refs = rv003usb/rv003usb.c):
//   * class requests land in usb_handle_other_control_message (:498-503,
//     RV003USB_OTHER_CONTROL=1); reply = e->opaque/max_len; leaving e zeroed
//     makes the stack ZLP/empty (= ACK).
//   * OUT capture: ist->setup_request=2 + e->opaque=[rxlen][data] (:369-388,
//     RV003USB_SUPPORT_CONTROL_OUT=1); stack stores wLength into word 0 on
//     completion; capture writes whole 8-byte packets -> 8 bytes of slack.
#ifndef _DFU_RV003USB_H
#define _DFU_RV003USB_H

#include "rv003usb.h"

// [rxlen][DFU_XFER_SIZE data][8 bytes slack for the partial final packet]
static uint32_t dfu_rx_buf[1 + DFU_XFER_SIZE/4 + 2] __attribute__((aligned(4)));

static uint8_t * dfu_rx_data( void )
{
	return (uint8_t *)&dfu_rx_buf[1];
}

static int dfu_rx_ready( uint32_t len )
{
	// Word 0 is written from the ISR on capture completion — volatile read.
	return *(volatile uint32_t *)&dfu_rx_buf[0] == len;
}

static void dfu_transport_init( void )
{
	usb_setup(); // per-target seam #2 in rv003usb.c: pins, pull-up, vector, IRQ
}

void usb_handle_other_control_message( struct usb_endpoint * e,
	struct usb_urb * s, struct rv003usb_internal * ist )
{
	const uint8_t * reply = 0;
	uint32_t replen = 0;

	switch( dfu_class_request( s->wRequestTypeLSBRequestMSB,
	                           s->lValueLSBIndexMSB & 0xffff,
	                           s->wLength, &reply, &replen ) )
	{
	case DFU_ACT_REPLY:
		e->opaque = (uint8_t *)reply;
		e->max_len = replen;
		break;
	case DFU_ACT_CAPTURE:
		dfu_rx_buf[0] = 0;
		e->opaque = (uint8_t *)dfu_rx_buf;
		e->max_len = s->wLength;
		e->count = 0;
		ist->setup_request = 2;
		break;
	default: // DFU_ACT_ACK: e stays zeroed -> stack ZLPs
		break;
	}
}

#endif // _DFU_RV003USB_H
