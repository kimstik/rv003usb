# Using USB on the CH32V003

Bit banged USB stack for the RISC-V WCH CH32V003.

Project is not ready for prime time.  Proof of concept works, we are on track for proper releases soon.  See the list below.  Can you help out?

:white_check_mark: Able to go on-bus and receive USB frames  
:white_check_mark: Compute CRC in-line while receiving data  
:white_check_mark: Bit Stuffing (Works, needs more testing)  
:white_check_mark: Sending USB Frames  
:white_check_mark: High Level USB Stack in C  
:white_check_mark: Make USB timing more precise.  
:white_check_mark: Rework sending code to send tokens/data separately.
:white_check_mark: Fix CRC Code  
:white_square_button: Further optimize Send/Receive PHY code.  
:white_square_button: Enable systick-based retiming to correct timing mid-frame.  
:white_check_mark: Optimize high-level stack.  
:white_square_button: Fit in bootloader  
:white_square_button: Use HID custom messages.  
:white_square_button: API For self-flashing + printf from bootloader
:large_orange_diamond: Enable retiming (Requires a few more cycles)  

