#ifndef _USB_CONFIG_H
#define _USB_CONFIG_H

// DFU runs entirely over EP0 — the config descriptor advertises zero
// endpoints and the stack's endpoint table only needs the control endpoint.
// (If a host ever refuses enumeration without an interrupt IN endpoint,
// bump this to 2 and add a dummy EP1 descriptor — not seen so far.)
#define ENDPOINTS 1

// K1921VG015: D+=C0, D-=C1, DPU=C2 (PLAN §7 defaults; external 1.5k DPU->D-).
#define USB_PORT C
#define USB_PIN_DP 0
#define USB_PIN_DM 1
#define USB_PIN_DPU 2

// Feature-flag set for the DFU loader.  Unlike bootloader_wg015 (HID blob
// protocol with the RV003USB_BOOTLOADER hooks), this loader is a plain
// rv003usb application: class (DFU) requests arrive through
// usb_handle_other_control_message and DNLOAD payload is captured with the
// generic control-out path (ist->setup_request = 2).
#define RV003USB_BOOTLOADER        0
#define RV003USB_OPTIMIZE_FLASH    1
#define RV003USB_EVENT_DEBUGGING   0
#define RV003USB_HANDLE_IN_REQUEST 0
#define RV003USB_OTHER_CONTROL     1
#define RV003USB_HANDLE_USER_DATA  0
#define RV003USB_HID_FEATURES      0
#define RV003USB_USB_TERMINAL      0 // #error on WG015 (pulls WCH swio_self.h)
#define RV003USB_USE_REBOOT_FEATURE_REPORT 0
#define RV003USB_USER_DATA_HANDLES_TOKEN 0
#define RV003USB_SUPPORT_CONTROL_OUT 1

#ifndef __ASSEMBLER__

#ifdef INSTANCE_DESCRIPTORS
// All descriptor payloads carry section(".rodata.usbdesc"): the WG015 linker
// scripts fold that into .data -> TCM-B, because descriptor bytes are read
// from inside the cycle-counted TX loop (PLAN Р3: clocked-path data in RAM).
#define USBDESC __attribute__((section(".rodata.usbdesc"), aligned(4)))

static const uint8_t device_descriptor[] USBDESC = {
	18, //Length
	1,  //Type (Device)
	0x00, 0x02, //Spec (bcdUSB)
	0x0, //Device Class (0 = per-interface; interface says DFU)
	0x0, //Device Subclass
	0x0, //Device Protocol
	0x08, //Max packet size for EP0 (8: USB Low-Speed)
	0x09, 0x12, //ID Vendor   (1209 pid.codes)
	0x03, 0xb0, //ID Product  (B003, same family as the WG015 HID loader)
	// bcdDevice 0x0201 = WG015 DFU protocol.  The HID blob loader reports
	// 0x0200, V003-family loaders 0x0000 — host tools gate on this.
	0x01, 0x02, //ID Rev (bcdDevice)
	1, //Manufacturer string
	2, //Product string
	3, //Serial string
	1, //Max number of configurations
};

static const uint8_t config_descriptor[] USBDESC = {
	// configuration descriptor, USB spec 9.6.3
	9, 					// bLength;
	2,					// bDescriptorType;
	0x1b, 0x00,			// wTotalLength = 9 + 9 + 9 = 27
	0x01,					// bNumInterfaces
	0x01,					// bConfigurationValue
	0x00,					// iConfiguration
	0x80,					// bmAttributes (bus powered)
	0x32,					// bMaxPower (100mA)

	// DFU-mode interface (DFU 1.1 spec 4.2.3): one interface, NO endpoints.
	9,					// bLength
	4,					// bDescriptorType (Interface)
	0,					// bInterfaceNumber
	0,					// bAlternateSetting
	0,					// bNumEndpoints (DFU uses EP0 only)
	0xFE,					// bInterfaceClass (Application Specific)
	0x01,					// bInterfaceSubClass (DFU)
	0x02,					// bInterfaceProtocol (DFU mode)
	0,					// iInterface

	// DFU functional descriptor (DFU 1.1 spec 4.1.3)
	9,					// bLength
	0x21,					// bDescriptorType (DFU FUNCTIONAL)
	0x03,					// bmAttributes: bitCanDnload | bitCanUpload
	0xFA, 0x00,			// wDetachTimeout (250 ms; DETACH is a no-op here)
	0x40, 0x00,			// wTransferSize = 64
	0x10, 0x01,			// bcdDFU 1.10
};

#define STR_MANUFACTURER u"cnlohr"
#define STR_PRODUCT      u"rv003usb"
#ifndef STR_SERIAL
#define STR_SERIAL       u"W15D" // WG015 DFU loader ("W015" = HID loader)
#endif

struct usb_string_descriptor_struct {
	uint8_t bLength;
	uint8_t bDescriptorType;
	const uint16_t wString[];
};

const static struct usb_string_descriptor_struct string0 USBDESC = {
	4,
	3,
	{0x0409}
};
const static struct usb_string_descriptor_struct string1 USBDESC = {
	sizeof(STR_MANUFACTURER),
	3,
	STR_MANUFACTURER
};
const static struct usb_string_descriptor_struct string2 USBDESC = {
	sizeof(STR_PRODUCT),
	3,
	STR_PRODUCT
};
const static struct usb_string_descriptor_struct string3 USBDESC = {
	sizeof(STR_SERIAL),
	3,
	STR_SERIAL
};

// This table defines which descriptor data is sent for each specific
// request from the host (in wValue and wIndex).
const static struct descriptor_list_struct {
	uint32_t	lIndexValue;
	const uint8_t	*addr;
	uint8_t		length;
} descriptor_list[] USBDESC = {
	{0x00000100, device_descriptor, sizeof(device_descriptor)},
	{0x00000200, config_descriptor, sizeof(config_descriptor)},
	{0x00000300, (const uint8_t *)&string0, 4},
	{0x04090301, (const uint8_t *)&string1, sizeof(STR_MANUFACTURER)},
	{0x04090302, (const uint8_t *)&string2, sizeof(STR_PRODUCT)},
	{0x04090303, (const uint8_t *)&string3, sizeof(STR_SERIAL)}
};
#define DESCRIPTOR_LIST_ENTRIES ((sizeof(descriptor_list))/(sizeof(struct descriptor_list_struct)) )

#endif // INSTANCE_DESCRIPTORS

#endif // __ASSEMBLER__
#endif
