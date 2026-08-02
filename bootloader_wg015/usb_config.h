#ifndef _USB_CONFIG_H
#define _USB_CONFIG_H

// Defines the number of endpoints for this device. (Always add one for EP0).
// Same as the upstream V003 bootloader.
#define ENDPOINTS 2

// K1921VG015: D+=C0, D-=C1, DPU=C2 (PLAN §7 defaults; external 1.5k DPU->D-).
#define USB_PORT C
#define USB_PIN_DP 0
#define USB_PIN_DM 1
#define USB_PIN_DPU 2

// Feature-flag set supported on WG015 (PLAN Р9/complete-5).  Unlike the V003
// bootloader (which carried its own usb_pid_handle_* copies), this loader
// links the shared rv003usb.c protocol layer with its RV003USB_BOOTLOADER
// hooks — the bootloader_v006 pattern.
#ifndef RV003USB_BOOTLOADER
#define RV003USB_BOOTLOADER    1
#endif
#define RV003USB_OPTIMIZE_FLASH    1
#define RV003USB_EVENT_DEBUGGING   0
#define RV003USB_HANDLE_IN_REQUEST 1
#define RV003USB_OTHER_CONTROL     0
#define RV003USB_HANDLE_USER_DATA  1
#define RV003USB_HID_FEATURES      1
#define RV003USB_USB_TERMINAL      0 // #error on WG015 (pulls WCH swio_self.h)
// The reboot feature report is for APPS to re-enter the loader; pointless
// (and 100+ bytes) inside the loader itself.  V003 bootloader had it off too.
#define RV003USB_USE_REBOOT_FEATURE_REPORT 0
// 0 (deviation from bootloader_v006's 1): let the shared layer ACK scratchpad
// DATA packets immediately — matches the V003 bootloader wire behavior and
// avoids one host-side retransmission per packet.
#define RV003USB_USER_DATA_HANDLES_TOKEN 0
#define RV003USB_SUPPORT_CONTROL_OUT 0

#ifndef __ASSEMBLER__

#include <tinyusb_hid.h>

#ifdef INSTANCE_DESCRIPTORS
// All descriptor payloads carry section(".rodata.usbdesc"): the WG015 linker
// scripts fold that into .data -> TCM-B, because descriptor bytes are read
// from inside the cycle-counted TX loop (PLAN Р3: clocked-path data in RAM).
#define USBDESC __attribute__((section(".rodata.usbdesc"), aligned(4)))

//Taken from http://www.usbmadesimple.co.uk/ums_ms_desc_dev.htm
static const uint8_t device_descriptor[] USBDESC = {
	18, //Length
	1,  //Type (Device)
	0x00, 0x02, //Spec (bcdUSB)
	0x0, //Device Class
	0x0, //Device Subclass
	0x0, //Device Protocol  (000 = use config descriptor)
	0x08, //Max packet size for EP0 (This has to be 8 because of the USB Low-Speed Standard)
	0x09, 0x12, //ID Vendor   (1209 pid.codes)
	0x03, 0xb0, //ID Product  (B003, same as the V003 bootloader)
	// bcdDevice 0x0200 = WG015 blob protocol (V003-family loaders report
	// 0x0000).  The host CLI MUST gate on this before sending any blob —
	// a wrong-chip blob is a brick vector (complete-8).
	0x00, 0x02, //ID Rev (bcdDevice)
	1, //Manufacturer string
	2, //Product string
	3, //Serial string
	1, //Max number of configurations
};

// Report IDs/sizes byte-identical to the V003 bootloader (wire compat):
// 0xa8 = 7 B, 0xaa = 127 B, 0xab = 1024+127 B feature reports.
static const uint8_t special_hid_desc[] USBDESC = {
  HID_USAGE_PAGE ( HID_USAGE_PAGE_DESKTOP ),
  HID_USAGE      ( 0xff ), // Needed?
  HID_REPORT_SIZE ( 8 ),
  HID_COLLECTION ( HID_COLLECTION_APPLICATION ),
    HID_REPORT_COUNT ( 7 ),
    HID_REPORT_ID    ( 0xa8 )
    HID_USAGE        ( 0xff ),
    HID_FEATURE      ( HID_DATA | HID_ARRAY | HID_ABSOLUTE ),
    HID_REPORT_COUNT ( 127 ),
    HID_REPORT_ID    ( 0xaa )
    HID_USAGE        ( 0xff ),
    HID_FEATURE      ( HID_DATA | HID_ARRAY | HID_ABSOLUTE ),
    HID_REPORT_COUNT_N ( 1024+127, 2 ),
    HID_REPORT_ID    ( 0xab )
    HID_USAGE        ( 0xff ),
    HID_FEATURE      ( HID_DATA | HID_ARRAY | HID_ABSOLUTE ),
  HID_COLLECTION_END
};

static const uint8_t config_descriptor[] USBDESC = {
	// configuration descriptor, USB spec 9.6.3, page 264-266, Table 9-10
	9, 					// bLength;
	2,					// bDescriptorType;
	0x22, 0x00,			// wTotalLength

	0x01,					// bNumInterfaces
	0x01,					// bConfigurationValue
	0x00,					// iConfiguration
	0x80,					// bmAttributes
	0x64,					// bMaxPower (200mA)

	//HID
	9,					// bLength
	4,					// bDescriptorType
	0,					// bInterfaceNumber
	0,					// bAlternateSetting
	1,					// bNumEndpoints
	0x03,					// bInterfaceClass (0x03 = HID)
	0x00,					// bInterfaceSubClass
	0xff,					// bInterfaceProtocol
	0,					// iInterface

	9,					// bLength
	0x21,					// bDescriptorType (HID)
	0x10,0x01,		// bcd 1.1
	0x00, // country code
	0x01, // Num descriptors
	0x22, // DescriptorType[0] (HID)
	sizeof(special_hid_desc), 0x00,

	7, // endpoint descriptor (For endpoint 1)
	0x05, // Endpoint Descriptor (Must be 5)
	0x81, // Endpoint Address
	0x03, // Attributes
	0x08, 0x00, // Size
	0xff, // Interval
};

#define STR_MANUFACTURER u"cnlohr"
#define STR_PRODUCT      u"rv003usb"
#ifndef STR_SERIAL
#define STR_SERIAL       u"W015" // WG015 loader ("NBTT" on V003)
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
	{0x00002200, special_hid_desc, sizeof(special_hid_desc)},
	{0x00000300, (const uint8_t *)&string0, 4},
	{0x04090301, (const uint8_t *)&string1, sizeof(STR_MANUFACTURER)},
	{0x04090302, (const uint8_t *)&string2, sizeof(STR_PRODUCT)},
	{0x04090303, (const uint8_t *)&string3, sizeof(STR_SERIAL)}
};
#define DESCRIPTOR_LIST_ENTRIES ((sizeof(descriptor_list))/(sizeof(struct descriptor_list_struct)) )

#endif // INSTANCE_DESCRIPTORS

#endif // __ASSEMBLER__
#endif
