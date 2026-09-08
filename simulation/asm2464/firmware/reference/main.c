/* Project-owned boundary stimulus; compiled for the upstream mcs51 architecture. */
#include "profile.h"
__sfr __at(0xA8) IE;
__sfr __at(0x96) DPX;
#define REG(a) (*(volatile __xdata unsigned char *)(a))
static void putc(unsigned char c) { while (!(REG(0xC009)&0x20)) {} REG(0xC000)=c; }
static void puts(const __code char *s) { while (*s) putc(*s++); }
static void hex(unsigned char v) { const __code char *h="0123456789ABCDEF"; putc(h[v>>4]);putc(h[v&15]); }
static void command(unsigned char op,unsigned int count) {
    REG(0xC8A3)=count>>8;REG(0xC8A4)=count;REG(0xC8AA)=op;REG(0xC8A9)=1;
    while(REG(0xC8A9)&1) {}
}
void main(void) {
    unsigned char a,b,c,status;
    IE=0;DPX=0;REG(0xC007)=3;
    puts("ASM2464 REF FW\nBUILD=" BUILD_ID "\n");
    command(0x9F,3);a=REG(0x7000);b=REG(0x7001);c=REG(0x7002);
    puts("FLASH_ID=");hex(a);hex(b);hex(c);putc('\n');
    command(5,1);status=REG(0x7000);puts("FLASH_STATUS=");hex(status);putc('\n');
    if(a!=JEDEC0 || b!=JEDEC1 || c!=JEDEC2 || (status&1)) {puts("BOOT=FAIL\n");for(;;) {}}
    /* Read a marker in external flash, through the actual upstream MMIO ABI. */
    REG(0xC8AB)=0;REG(0xC8A2)=0;REG(0xC8A1)=0x80;command(3,4);
    if(REG(0x7000)!='R'||REG(0x7001)!='E'||REG(0x7002)!='F'||REG(0x7003)!='1') {puts("BOOT=FAIL\n");for(;;) {}}
    puts("BOOT=OK\n");for(;;) {}
}
