#ifndef _FUNCONFIG_H
#define _FUNCONFIG_H

#define FUNCONF_USE_DEBUGPRINTF  0
/* Satisfied by construction on WG015: the tick source is rdcycle, which is
 * HCLK-synchronous (shim ch32fun.h). */
#define FUNCONF_SYSTICK_USE_HCLK 1

#endif
