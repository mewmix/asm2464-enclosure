#ifndef NVME_ADMIN_H
#define NVME_ADMIN_H

#include "types.h"
#include "registers.h"

/* Stock-observed PCI addresses and CPU XDATA windows. Their intervening ASIC
 * translation remains a cross-release inference. */
#define NVME_ADMIN_BAR0          0x00D00000UL
#define NVME_ADMIN_SQ_DMA        0x00800000UL
#define NVME_ADMIN_CQ_DMA        0x00808000UL
#define NVME_ADMIN_DATA_OUT_DMA  0x00200400UL
#define NVME_ADMIN_DATA_IN_DMA   0x00200000UL
#define NVME_IDENTIFY_DATA_IN_DMA 0x00200000UL
#define NVME_STOCK_SQE_XDATA     ((__xdata uint8_t *)0xA000)
#define NVME_STOCK_CQ_XDATA      ((__xdata uint8_t *)0xB800)
#define NVME_ADMIN_SQ_XDATA      NVME_STOCK_SQE_XDATA
#define NVME_ADMIN_CQ_XDATA      NVME_STOCK_CQ_XDATA
#define NVME_ADMIN_DATA_OUT      ((__xdata uint8_t *)0xF400)
#define NVME_ADMIN_DATA_IN       ((__xdata uint8_t *)0xF000)
#define NVME_IDENTIFY_DATA_IN    ((__xdata uint8_t *)0xF000)
#define NVME_ADMIN_QUEUE_DEPTH   4

#define NVME_REG_CAP             0x00
#define NVME_REG_VS              0x08
#define NVME_REG_CC              0x14
#define NVME_REG_CSTS            0x1C
#define NVME_REG_AQA             0x24
#define NVME_REG_ASQ             0x28
#define NVME_REG_ACQ             0x30
#define NVME_REG_SQ0TDBL         0x1000

#define NVME_ADMIN_IDENTIFY      0x06
#define NVME_ADMIN_CREATE_IO_SQ  0x01
#define NVME_ADMIN_CREATE_IO_CQ  0x05
#define NVME_ADMIN_SECURITY_SEND 0x81
#define NVME_ADMIN_SECURITY_RECV 0x82

#define NVME_ADMIN_OK            0x00
#define NVME_ADMIN_ERR_OPCODE    0x01
#define NVME_ADMIN_ERR_TIMEOUT   0x02
#define NVME_ADMIN_ERR_STATUS    0x03
#define NVME_ADMIN_ERR_FATAL     0x04
#define NVME_ADMIN_ERR_LENGTH    0x05
#define NVME_ADMIN_ERR_CONFIG    0x06

typedef struct {
  volatile uint8_t initialized;
  uint8_t last_error;
  uint8_t last_opcode;
  uint8_t last_nvme_status;
  uint16_t last_cq_status;
  uint16_t command_id;
  uint8_t sq_tail;
  uint8_t cq_head;
  uint8_t cq_phase;
  uint8_t doorbell_stride;
  volatile uint8_t generation;
} nvme_admin_state_t;

typedef struct {
  nvme_admin_state_t state;
  uint32_t cdw10;
  uint32_t cdw11;
  uint32_t prp1;
  uint32_t nsid;
  uint16_t length;
  uint8_t security_protocol;
  uint16_t security_comid;
  uint16_t security_length;
  uint8_t mmio_scratch[4];
  uint8_t diag_error;
  uint16_t diag_length;
} nvme_admin_workspace_t;

static __xdata __at (0xA800) nvme_admin_workspace_t nvme_admin_workspace;
#define nvme_admin_state       nvme_admin_workspace.state
#define nvme_arg_cdw10         nvme_admin_workspace.cdw10
#define nvme_arg_cdw11         nvme_admin_workspace.cdw11
#define nvme_arg_prp1          nvme_admin_workspace.prp1
#define nvme_arg_nsid          nvme_admin_workspace.nsid
#define nvme_arg_length        nvme_admin_workspace.length
#define nvme_security_protocol nvme_admin_workspace.security_protocol
#define nvme_security_comid    nvme_admin_workspace.security_comid
#define nvme_security_length   nvme_admin_workspace.security_length
#define nvme_diag_error        nvme_admin_workspace.diag_error
#define nvme_diag_length       nvme_admin_workspace.diag_length

static void nvme_put_le16(__xdata uint8_t *p, uint16_t value) __reentrant {
  p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
}

static void nvme_put_le32(__xdata uint8_t *p, uint32_t value) __reentrant {
  p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
  p[2] = (uint8_t)(value >> 16); p[3] = (uint8_t)(value >> 24);
}

static uint32_t nvme_get_le32(__xdata const uint8_t *p) __reentrant {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void nvme_pcie_addr(uint16_t offset, uint8_t write) __naked {
  (void)offset; (void)write;
  __asm
    mov   r6, dpl
    mov   r7, dph
    mov   a, (_nvme_pcie_addr_PARM_2)
    jz    00001$
    mov   a, #0x40
  00001$:
    mov   dptr, #0xb210
    movx  @dptr, a
    mov   dptr, #0xb213
    mov   a, #0x01
    movx  @dptr, a
    mov   dptr, #0xb216
    mov   a, #0x20
    movx  @dptr, a
    inc   dptr
    mov   a, #0x0f
    movx  @dptr, a
    inc   dptr
    clr   a
    movx  @dptr, a
    inc   dptr
    mov   a, #0xd0
    movx  @dptr, a
    inc   dptr
    mov   a, r7
    movx  @dptr, a
    inc   dptr
    mov   a, r6
    movx  @dptr, a
    ret
  __endasm;
}

static uint32_t nvme_mmio_read32(uint16_t offset) __naked {
  (void)offset;
  __asm
    mov   (_nvme_pcie_addr_PARM_2), #0x00
    lcall _nvme_pcie_addr
    mov   dpl, #0x00
    lcall _pcie_tlp_exec
    mov   a, dpl
    jz    00010$
    mov   dpl, #0xff
    mov   dph, #0xff
    mov   b, #0xff
    mov   a, #0xff
    ret
  00010$:
    ; Read little-endian value from B223..B220 and retain the diagnostic copy.
    mov   dptr, #0xb223
    movx  a, @dptr
    mov   r4, a
    dec   dpl
    movx  a, @dptr
    mov   r5, a
    dec   dpl
    movx  a, @dptr
    mov   r6, a
    dec   dpl
    movx  a, @dptr
    mov   r7, a
    mov   dptr, #0xa824
    mov   a, r4
    movx  @dptr, a
    inc   dptr
    mov   a, r5
    movx  @dptr, a
    inc   dptr
    mov   a, r6
    movx  @dptr, a
    inc   dptr
    mov   a, r7
    movx  @dptr, a
    mov   dpl, r4
    mov   dph, r5
    mov   b, r6
    mov   a, r7
    ret
  __endasm;
}

static uint8_t nvme_mmio_write32(uint16_t offset, uint32_t value) __reentrant {
  nvme_pcie_addr(offset, 1);
  nvme_put_le32(nvme_admin_workspace.mmio_scratch, value);
  REG_PCIE_DATA_0 = nvme_admin_workspace.mmio_scratch[3];
  REG_PCIE_DATA_1 = nvme_admin_workspace.mmio_scratch[2];
  REG_PCIE_DATA_2 = nvme_admin_workspace.mmio_scratch[1];
  REG_PCIE_DATA_3 = nvme_admin_workspace.mmio_scratch[0];
  return pcie_tlp_exec(PCIE_FMT_MEM_WRITE);
}

static void nvme_zero(__xdata uint8_t *p, uint16_t length) __naked {
  (void)p; (void)length;
  __asm
    push  ar4
    push  ar5
    push  ar6
    push  ar7
    mov   r6, dpl
    mov   r7, dph
    mov   r4, (_nvme_zero_PARM_2)
    mov   r5, (_nvme_zero_PARM_2 + 1)
  00020$:
    mov   a, r4
    orl   a, r5
    jz    00025$
    mov   dpl, r6
    mov   dph, r7
    clr   a
    movx  @dptr, a
    inc   dptr
    mov   r6, dpl
    mov   r7, dph
    dec   r4
    cjne  r4, #0xff, 00020$
    dec   r5
    sjmp  00020$
  00025$:
    pop   ar7
    pop   ar6
    pop   ar5
    pop   ar4
    ret
  __endasm;
}

static uint8_t nvme_wait_csts(uint8_t ready) __reentrant {
  uint16_t attempt;
  for (attempt = 0; attempt < 1000; attempt++) {
    uint32_t csts = nvme_mmio_read32(NVME_REG_CSTS);
    if (csts == 0xFFFFFFFFUL) return NVME_ADMIN_ERR_TIMEOUT;
    if (csts & 0x02) return NVME_ADMIN_ERR_FATAL;
    if ((uint8_t)(csts & 1) == ready) return NVME_ADMIN_OK;
    sleep(1);
  }
  return NVME_ADMIN_ERR_TIMEOUT;
}

static uint8_t nvme_admin_init(void) __reentrant {
  uint32_t cc;
  uint8_t error;
  uint8_t generation = nvme_admin_state.generation;

  nvme_admin_state.initialized = 0;
  nvme_admin_state.last_error = NVME_ADMIN_OK;
  nvme_admin_state.last_nvme_status = 0;
  nvme_admin_state.last_cq_status = 0;

  /* 0. Pre-BAR PCIe Bridge & Endpoint Configuration */
  error = pcie_pre_bar_init();
  if (error != 0) {
    return nvme_admin_state.last_error = NVME_ADMIN_ERR_CONFIG;
  }

  /* Keep the telemetry field layout, but the ASIC selector path never uses
   * a CPU-computed MMIO doorbell stride. Zero denotes unused here. */
  nvme_admin_state.doorbell_stride = 0;

  cc = nvme_mmio_read32(NVME_REG_CC);
  if (cc == 0xFFFFFFFFUL) return nvme_admin_state.last_error = NVME_ADMIN_ERR_TIMEOUT;
  if (cc & 1) {
    if (nvme_mmio_write32(NVME_REG_CC, cc & ~1UL) != 0)
      return nvme_admin_state.last_error = NVME_ADMIN_ERR_FATAL;
  }
  /* Even an already-clear CC.EN can precede CSTS.RDY falling. Do not
   * replace queue registers until controller disable has completed. */
  if ((error = nvme_wait_csts(0)) != 0) return nvme_admin_state.last_error = error;

  /* Cold USB bring-up already runs exact CF91: B264..267=08,00,08,08;
   * B26C..26F=08,20,08,28, followed by event/mask/routing setup. Preserve
   * that ordering. The B26x address encoding remains inferred. B80x is a
   * controller-selected completion view, not CPU-owned RAM to memset. */
  REG_CPU_LINK_CEF3 = 0x08; /* Q1 event, NOT a Q0 error */
  REG_CPU_LINK_CEF2 = 0x80;
  REG_PCIE_STATUS = PCIE_STATUS_ERROR;
  REG_PCIE_STATUS = PCIE_STATUS_COMPLETE;
  REG_PCIE_STATUS = PCIE_STATUS_ENGINE_DONE;
  if (generation != nvme_admin_state.generation)
    return nvme_admin_state.last_error = NVME_ADMIN_ERR_FATAL;
  if (nvme_mmio_write32(NVME_REG_AQA, 0x00030003UL) != 0 ||
      nvme_mmio_write32(NVME_REG_ASQ, NVME_ADMIN_SQ_DMA) != 0 ||
      nvme_mmio_write32(NVME_REG_ASQ + 4, 0) != 0 ||
      nvme_mmio_write32(NVME_REG_ACQ, NVME_ADMIN_CQ_DMA) != 0 ||
      nvme_mmio_write32(NVME_REG_ACQ + 4, 0) != 0 ||
      nvme_mmio_write32(NVME_REG_CC, (6UL << 16) | (4UL << 20) | 1UL) != 0)
    return nvme_admin_state.last_error = NVME_ADMIN_ERR_FATAL;
  if ((error = nvme_wait_csts(1)) != 0) return nvme_admin_state.last_error = error;
  error = NVME_ADMIN_ERR_FATAL;
  /* Serialize only software publication against USB reset. All ASIC waits
   * above remain interruptible, including the CSTS transition. */
  __critical {
    if (generation == nvme_admin_state.generation) {
      nvme_admin_state.sq_tail = 0; nvme_admin_state.cq_head = 0;
      /* Stock stores the stale phase: equality stops, inequality is fresh. */
      nvme_admin_state.cq_phase = 0;
      nvme_admin_state.generation++;
      nvme_admin_state.command_id = (uint16_t)nvme_admin_state.generation << 8;
      nvme_admin_state.initialized = 1;
      error = NVME_ADMIN_OK;
    }
  }
  return nvme_admin_state.last_error = error;
}

static uint8_t nvme_stock_ring(uint8_t val, uint8_t command) __naked {
  (void)val; (void)command;
  __asm
    mov   a, dpl
    mov   r7, a
    ; An old engine-done latch must not satisfy this new notification.
    mov   dptr, #0xB296
    mov   a, #0x01
    movx  @dptr, a
    mov   a, #0x04
    movx  @dptr, a
    mov   dptr, #0xB251
    mov   a, r7
    movx  @dptr, a
    inc   dptr                                      ; B252
    inc   dptr                                      ; B253
    inc   dptr                                      ; B254
    mov   a, (_nvme_stock_ring_PARM_2)
    movx  @dptr, a
    mov   r6, #0x00                                 ; 256 bounded reads
  00020$:
    mov   dptr, #0xB296
    movx  a, @dptr
    jb    acc.0, 00030$
    jb    acc.2, 00040$
    djnz  r6, 00020$
    mov   dpl, #0x02                                ; timeout
    ret
  00030$:
    anl   a, #0x05                                  ; ACK error and done if set
    movx  @dptr, a
    mov   dpl, #0x04                                ; fatal transport error
    ret
  00040$:
    mov   a, #0x04
    movx  @dptr, a
    mov   dpl, #0x00
    ret
  __endasm;
}

static uint8_t nvme_admin_command(uint8_t opcode) __reentrant {
  __xdata uint8_t *sqe;
  __xdata volatile uint8_t *cqe;
  uint8_t i;
  uint8_t loop_hi, loop_lo, error, generation;
  uint16_t cid;
  uint16_t status, attempt;

  if (opcode != NVME_ADMIN_IDENTIFY &&
      opcode != NVME_ADMIN_CREATE_IO_SQ &&
      opcode != NVME_ADMIN_CREATE_IO_CQ &&
      opcode != NVME_ADMIN_SECURITY_SEND &&
      opcode != NVME_ADMIN_SECURITY_RECV) return NVME_ADMIN_ERR_OPCODE;
  if (!nvme_admin_state.initialized) return NVME_ADMIN_ERR_FATAL;
  generation = nvme_admin_state.generation;
  if (nvme_arg_length > 4096 || (nvme_arg_length & 3)) return NVME_ADMIN_ERR_LENGTH;

  nvme_admin_state.last_opcode = opcode;
  nvme_admin_state.last_cq_status = 0xFFFFU;
  nvme_admin_state.last_nvme_status = 0;
  nvme_admin_state.command_id++;

  /* Stock 23E8 -> 15F2 -> 4AA0: selector 0, B000+64*old Q0
   * producer, count 64. Preserve separate mode writes. Only one endpoint
   * is proven; this is not a claimed general-purpose DMA copy. */
  REG_DMA_STATUS2 &= (uint8_t)~0x02;
  REG_DMA_CHAN_STATUS2 = 0;
  REG_DMA_CHAN_CTRL2 = (REG_DMA_CHAN_CTRL2 & 0xFBU) | 0x04U;
  REG_DMA_CHAN_CTRL2 &= (uint8_t)~0x01;
  REG_DMA_CHAN_CTRL2 &= (uint8_t)~0x02;
  REG_DMA_CHAN_CTRL2 = (REG_DMA_CHAN_CTRL2 & 0x7FU) | 0x80U;
  REG_DMA_CHAN_AUX = 0xB0;
  REG_DMA_CHAN_AUX1 = nvme_admin_state.sq_tail << 6;
  REG_DMA_XFER_CNT_HI = 0;
  REG_DMA_XFER_CNT_LO = 63;
  REG_DMA_TRIGGER = 1;
  for (attempt = 0; attempt < 65535U; attempt++) {
    if (!nvme_admin_state.initialized ||
        nvme_admin_state.generation != generation) break;
    if (!(XDATA_REG8V(0xC8B8) & 1)) break;
  }
  REG_DMA_CHAN_CTRL2 &= (uint8_t)~0x80;
  if (attempt == 65535U || !nvme_admin_state.initialized ||
      nvme_admin_state.generation != generation) {
    error = NVME_ADMIN_ERR_TIMEOUT;
    goto poison;
  }
  REG_DMA_STATUS2 = (REG_DMA_STATUS2 & 0xFEU) | 1U;
  REG_DMA_CTRL = 0x80U | nvme_admin_state.sq_tail;

  /* Stock writes the selected Admin entry through the fixed A000 view. */
  sqe = NVME_STOCK_SQE_XDATA;
  for (i = 0; i < 64; i++) sqe[i] = 0;
  sqe[0] = opcode;
  nvme_put_le16(sqe + 2, nvme_admin_state.command_id);
  nvme_put_le32(sqe + 4, nvme_arg_nsid);
  nvme_put_le32(sqe + 24, nvme_arg_prp1);
  nvme_put_le32(sqe + 40, nvme_arg_cdw10);
  nvme_put_le32(sqe + 44, nvme_arg_cdw11);
  REG_DMA_STATUS2 &= (uint8_t)~0x01;

  /* 2 & 3. Submit through the stock-observed Queue-0 doorbell path. */
  REG_CPU_LINK_CEF2 = 0x80; /* stale event from an earlier command */
  nvme_admin_state.sq_tail = (uint8_t)((nvme_admin_state.sq_tail + 1) & 0x03);
  error = nvme_stock_ring(nvme_admin_state.sq_tail, PCIE_TRIGGER_SQ0);
  if (error) goto poison;

  /* 4. Await the Queue-0 event. C806.5 is used by stock's asynchronous ISR,
   * but is not sufficient by itself to identify this command's completion. */
  for (loop_hi = 0; loop_hi < 20; loop_hi++) {
    for (loop_lo = 0; loop_lo != 255; loop_lo++) {
      if (!nvme_admin_state.initialized ||
          nvme_admin_state.generation != generation) {
        error = NVME_ADMIN_ERR_FATAL;
        goto poison;
      }
      if (REG_CPU_LINK_CEF2 & 0x80) goto cpl_done;
    }
  }
  error = NVME_ADMIN_ERR_TIMEOUT;
  goto poison;

cpl_done:
  REG_CPU_LINK_CEF2 = 0x80; /* acknowledge CEF2.7 with 0x80 (W1C) */

  /* 5. Consume CQE at 0xB800 + 16 * cq_head */
  REG_DMA_STATUS = (REG_DMA_STATUS & 0xF7U) | 0x08U;
  REG_DMA_STATUS &= (uint8_t)~0x04;
  cqe = (__xdata volatile uint8_t *)NVME_STOCK_CQ_XDATA +
        ((uint16_t)nvme_admin_state.cq_head << 4);

  /* Verify freshness using CQE byte 14 bit 0 and expected phase */
  if ((cqe[14] & 0x01) == nvme_admin_state.cq_phase) {
    error = NVME_ADMIN_ERR_TIMEOUT;
    goto poison;
  }

  /* Validate CID (bytes 12..13) */
  cid = (uint16_t)cqe[12] | ((uint16_t)cqe[13] << 8);
  if (cid != nvme_admin_state.command_id) {
    error = NVME_ADMIN_ERR_STATUS;
    goto poison;
  }

  /* Decode status field (bytes 14..15) */
  status = (uint16_t)cqe[14] | ((uint16_t)cqe[15] << 8);
  nvme_admin_state.last_cq_status = status;
  nvme_admin_state.last_nvme_status = (uint8_t)(((cqe[14] >> 1) & 0x7F) | ((cqe[15] & 0x01) << 7));
  /* SC and SCT determine success; CRD/M/DNR are not status codes. */
  nvme_admin_state.last_error = (status & 0x0FFEU) ? NVME_ADMIN_ERR_STATUS : NVME_ADMIN_OK;

  /* 6. Advance CQ head modulo 4: toggle expected phase on wrap, write B251, trigger B254=0x11 */
  nvme_admin_state.cq_head = (uint8_t)((nvme_admin_state.cq_head + 1) & 0x03);
  if (!nvme_admin_state.cq_head) nvme_admin_state.cq_phase ^= 1;

  error = nvme_stock_ring(nvme_admin_state.cq_head, PCIE_TRIGGER_CQ0);
  REG_DMA_STATUS &= (uint8_t)~0x08;
  REG_DMA_STATUS &= (uint8_t)~0x04;
  if (error) goto poison;

  return nvme_admin_state.last_error;

poison:
  REG_DMA_STATUS2 &= (uint8_t)~0x01;
  REG_DMA_STATUS &= (uint8_t)~0x08;
  REG_DMA_STATUS &= (uint8_t)~0x04;
  nvme_admin_state.initialized = 0;
  return nvme_admin_state.last_error = error;
}

static uint8_t nvme_identify_controller(void) __reentrant {
  nvme_zero(NVME_IDENTIFY_DATA_IN, 4096);
  nvme_arg_nsid = 0; nvme_arg_cdw10 = 1; nvme_arg_cdw11 = 0;
  nvme_arg_prp1 = NVME_IDENTIFY_DATA_IN_DMA; nvme_arg_length = 4096;
  return nvme_admin_command(NVME_ADMIN_IDENTIFY);
}

static uint8_t nvme_identify_namespace(uint32_t nsid) __reentrant {
  if (!nsid || nsid == 0xFFFFFFFFUL) return NVME_ADMIN_ERR_CONFIG;
  nvme_zero(NVME_IDENTIFY_DATA_IN, 4096);
  nvme_arg_nsid = nsid; nvme_arg_cdw10 = 0; nvme_arg_cdw11 = 0;
  nvme_arg_prp1 = NVME_IDENTIFY_DATA_IN_DMA; nvme_arg_length = 4096;
  return nvme_admin_command(NVME_ADMIN_IDENTIFY);
}

static uint8_t nvme_security_receive(void) __reentrant {
  if (!nvme_security_length || nvme_security_length > 512 || (nvme_security_length & 3)) return NVME_ADMIN_ERR_LENGTH;
  /* Serialized Q0 receive shares Identify's F000/00200000 data window.
   * This removes the synthetic A400 alias. Security payload delivery still
   * needs operator validation; no ASIC copy/completion is synthesized. */
  nvme_zero(NVME_ADMIN_DATA_IN, nvme_security_length);
  nvme_arg_nsid = 0;
  nvme_arg_cdw10 = ((uint32_t)nvme_security_protocol << 24) | ((uint32_t)nvme_security_comid << 8);
  nvme_arg_cdw11 = nvme_security_length; nvme_arg_prp1 = NVME_ADMIN_DATA_IN_DMA;
  nvme_arg_length = nvme_security_length;
  return nvme_admin_command(NVME_ADMIN_SECURITY_RECV);
}

static uint8_t nvme_security_send(void) __reentrant {
  if (!nvme_security_length || nvme_security_length > 512 || (nvme_security_length & 3)) return NVME_ADMIN_ERR_LENGTH;
  nvme_arg_nsid = 0;
  nvme_arg_cdw10 = ((uint32_t)nvme_security_protocol << 24) | ((uint32_t)nvme_security_comid << 8);
  nvme_arg_cdw11 = nvme_security_length; nvme_arg_prp1 = NVME_ADMIN_DATA_OUT_DMA;
  nvme_arg_length = nvme_security_length;
  return nvme_admin_command(NVME_ADMIN_SECURITY_SEND);
}

#endif /* NVME_ADMIN_H */
