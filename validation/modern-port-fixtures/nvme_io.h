#ifndef MAYFLOWER_NVME_IO_H
#define MAYFLOWER_NVME_IO_H

#include "types.h"
#include "nvme_admin.h"

/* Serialized BOT adapter: retain bounded transfer sizes. Stock proves the
 * READ PRP value, not its physical mapping to a CPU or USB data window. */
#define NVME_IO_QID          1U
#define NVME_IO_QUEUE_DEPTH  32U
#define NVME_IO_MAX_READ_BLOCKS  2U
#define NVME_IO_MAX_WRITE_BLOCKS 6U
#define NVME_IO_SQ_DMA       0x00820000UL
#define NVME_IO_CQ_DMA       0x00828000UL
#define NVME_IO_DATA_OUT_DMA NVME_ADMIN_DATA_OUT_DMA
#define NVME_IO_DATA_IN_DMA  0x00200000UL
#define NVME_IO_STAGING_BASE(context) ((context) == 1U ? 0xA800U : 0xA000U)
#define NVME_IO_PRODUCER(context) (PCIE_TRIGGER_SQ1 + (context))
#define NVME_IO_CONSUMER(context) (PCIE_TRIGGER_CQ1 + (context))
/* Context 1 aliases chassis state at A800..A8CD. Its identity is unresolved;
 * do not activate it or relocate the proven USB/recovery workspaces. */
#define NVME_IO_CONTEXT 0U
#if NVME_IO_CONTEXT != 0
#error "Alternate NVMe context overlaps the USB/BOT chassis workspace"
#endif
#define NVME_IO_DATA_OUT     NVME_ADMIN_DATA_OUT

#define NVME_IO_FLUSH 0x00U
#define NVME_IO_WRITE 0x01U
#define NVME_IO_READ  0x02U

#define NVME_IO_OK             0x00U
#define NVME_IO_ERR_OPCODE     0x01U
#define NVME_IO_ERR_TIMEOUT    0x02U
#define NVME_IO_ERR_STATUS     0x03U
#define NVME_IO_ERR_ADMIN      0x04U
#define NVME_IO_ERR_STALE      0x05U
#define NVME_IO_ERR_TRANSPORT  0x06U

typedef struct {
  volatile uint8_t initialized;
  uint8_t last_error;
  uint8_t last_nvme_status;
  uint8_t admin_generation;
  uint16_t command_id;
  uint8_t sq_tail;
  uint8_t cq_head;
  uint8_t cq_phase;
  uint16_t last_cq_status;
#if MAYFLOWER_NVME_IO_TEST
  uint8_t diag_request;
  uint8_t diag_done;
  uint16_t diag_lba;
#endif
} nvme_io_workspace_t;
typedef char nvme_io_workspace_must_fit[(sizeof(nvme_io_workspace_t) <= 0x15U) ? 1 : -1];

/* nvme_admin_workspace occupies A800..A82A. Keep this workspace below the
 * BOT workspace at A840. */
static __xdata __at (0xA82B) nvme_io_workspace_t nvme_io_workspace;

static void nvme_io_reset(void) {
  nvme_io_workspace.initialized = 0;
  nvme_io_workspace.last_error = NVME_IO_OK;
  nvme_io_workspace.last_nvme_status = 0;
  nvme_io_workspace.last_cq_status = 0xFFFFU;
  nvme_io_workspace.sq_tail = 0;
  nvme_io_workspace.cq_head = 0;
  nvme_io_workspace.cq_phase = 0;
  nvme_admin_state.initialized = 0;
  /* Revokes a concurrent Admin rebuild as well as existing queue owners. */
  nvme_admin_state.generation++;
#if MAYFLOWER_NVME_IO_TEST
  nvme_io_workspace.diag_request = 0;
  nvme_io_workspace.diag_done = 0;
#endif
}

static bool nvme_io_ready(void) {
  return nvme_io_workspace.initialized &&
         nvme_admin_state.initialized &&
         nvme_io_workspace.admin_generation == nvme_admin_state.generation;
}

static uint8_t nvme_io_init(void) __reentrant {
  uint8_t error;
  uint8_t generation = nvme_admin_state.generation;
  if (nvme_io_ready()) return NVME_IO_OK;
  nvme_io_workspace.initialized = 0;
  if (!nvme_admin_state.initialized)
    return nvme_io_workspace.last_error = NVME_IO_ERR_ADMIN;

  nvme_arg_nsid = 0;
  nvme_arg_prp1 = NVME_IO_CQ_DMA;
  nvme_arg_cdw10 = ((uint32_t)(NVME_IO_QUEUE_DEPTH - 1U) << 16) | NVME_IO_QID;
  nvme_arg_cdw11 = 1U; /* physically contiguous, interrupts disabled */
  nvme_arg_length = 0;
  error = nvme_admin_command(NVME_ADMIN_CREATE_IO_CQ);
  if (error) {
    nvme_admin_state.initialized = 0;
    return nvme_io_workspace.last_error = NVME_IO_ERR_ADMIN;
  }

  nvme_arg_prp1 = NVME_IO_SQ_DMA;
  nvme_arg_cdw11 = ((uint32_t)NVME_IO_QID << 16) | 5U;
  error = nvme_admin_command(NVME_ADMIN_CREATE_IO_SQ);
  if (error) {
    /* CQ may exist without SQ. Rebuild Q0/controller before retrying. */
    nvme_admin_state.initialized = 0;
    return nvme_io_workspace.last_error = NVME_IO_ERR_ADMIN;
  }

  error = NVME_IO_ERR_STALE;
  __critical {
    if (nvme_admin_state.initialized &&
        generation == nvme_admin_state.generation) {
      /* Publish only the Admin generation that created both queues. */
      nvme_io_workspace.command_id = (uint16_t)generation << 8;
      nvme_io_workspace.sq_tail = 0;
      nvme_io_workspace.cq_head = 0;
      nvme_io_workspace.cq_phase = 0; /* stale phase, as stock 0466 */
      nvme_io_workspace.last_nvme_status = 0;
      nvme_io_workspace.last_cq_status = 0xFFFFU;
      nvme_io_workspace.admin_generation = generation;
      nvme_io_workspace.initialized = 1;
      error = NVME_IO_OK;
    }
  }
  return nvme_io_workspace.last_error = error;
}

static uint8_t nvme_io_command(uint8_t opcode, uint32_t lba,
                               uint8_t blocks) __reentrant {
  __xdata uint8_t *sqe;
  uint8_t i, loop_hi, loop_lo, error;
  uint8_t next_head, next_phase;
  uint16_t cid, status;
  if (opcode > NVME_IO_READ) goto bad_opcode;
  if (opcode == NVME_IO_FLUSH) {
    if (blocks != 0) goto bad_opcode;
  } else {
    if (blocks == 0) goto bad_opcode;
    if (opcode == NVME_IO_READ) {
      if (blocks > NVME_IO_MAX_READ_BLOCKS) goto bad_opcode;
    } else if (blocks > NVME_IO_MAX_WRITE_BLOCKS) {
      goto bad_opcode;
    }
  }
  if (!nvme_io_ready())
    return nvme_io_workspace.last_error = NVME_IO_ERR_STALE;

  nvme_io_workspace.command_id++;
  nvme_io_workspace.last_nvme_status = 0;
  nvme_io_workspace.last_cq_status = 0xFFFFU;

  /* Stock 1A80: stage at OLD cursor; B251 receives the NEXT cursor. */
  sqe = (__xdata uint8_t *)(NVME_IO_STAGING_BASE(NVME_IO_CONTEXT) +
                           ((uint16_t)nvme_io_workspace.sq_tail << 6));
  for (i = 0; i < 64; i++) sqe[i] = 0;
  sqe[0] = opcode;
  nvme_put_le16(sqe + 2, nvme_io_workspace.command_id);
  sqe[4] = 1; /* namespace 1 */
  if (opcode != NVME_IO_FLUSH) {
    nvme_put_le32(sqe + 24, opcode == NVME_IO_READ ?
                  NVME_IO_DATA_IN_DMA : NVME_IO_DATA_OUT_DMA);
    nvme_put_le32(sqe + 40, lba);
    sqe[48] = blocks - 1U;
  }

  /* 2 & 3. Submit through the stock Queue-1 doorbell path. */
  nvme_io_workspace.sq_tail =
      (uint8_t)((nvme_io_workspace.sq_tail + 1U) &
                (NVME_IO_QUEUE_DEPTH - 1U));
  error = nvme_stock_ring(nvme_io_workspace.sq_tail,
                          NVME_IO_PRODUCER(NVME_IO_CONTEXT));
  if (error) {
    error = NVME_IO_ERR_TRANSPORT;
    goto poison;
  }

  /* Stock 25F9 has direct/polling callers as well as INT0. Acknowledge the
   * observed Q1 event before parsing if present; B296 is only notification
   * ACK and B294 is not a Q1 completion source. Never fabricate a CQE. */
  for (loop_hi = 0; loop_hi < 30; loop_hi++) {
    for (loop_lo = 0; loop_lo != 0xFF; loop_lo++) {
      if (!nvme_io_ready()) {
        error = NVME_IO_ERR_STALE;
        goto poison;
      }
      if ((XDATA_REG8V(0xC806) & 0x20U) &&
          (REG_CPU_LINK_CEF3 & 0x08U)) REG_CPU_LINK_CEF3 = 0x08;
      REG_DMA_STATUS = (REG_DMA_STATUS & 0xFEU) | 1U;
      REG_DMA_QUEUE_IDX = nvme_io_workspace.cq_head +
                          (NVME_IO_CONTEXT ? 32U : 0U);
      status = XDATA_REG8V(0xB80E);
      if ((status & 1U) != nvme_io_workspace.cq_phase) {
        goto got_completion;
      }
      REG_DMA_STATUS &= (uint8_t)~0x01;
    }
  }
  error = NVME_IO_ERR_TIMEOUT;
  goto poison;

got_completion:
  /* Fixed B80C..F view selected above, not B840 + head*16. Keep the two
   * identifier bytes separate from the staging index. */
  cid = XDATA_REG8V(0xB80C);
  cid |= (uint16_t)XDATA_REG8V(0xB80D) << 8;
  status |= (uint16_t)XDATA_REG8V(0xB80F) << 8;
  REG_DMA_STATUS &= (uint8_t)~0x01;
  if (!nvme_io_ready() || cid != nvme_io_workspace.command_id) {
    error = NVME_IO_ERR_STALE;
    goto poison;
  }
  nvme_io_workspace.last_cq_status = status;
  nvme_io_workspace.last_nvme_status = (uint8_t)(status >> 1);

  /* One outstanding BOT command means at most one entry is consumed per
   * call. The stock full-32-entry scan ambiguity cannot occur here. Notify
   * first, then persist the changed head/phase (stock 27D9:27FA). */
  next_head =
      (uint8_t)((nvme_io_workspace.cq_head + 1U) &
                (NVME_IO_QUEUE_DEPTH - 1U));
  next_phase = nvme_io_workspace.cq_phase;
  if (!next_head) next_phase ^= 1U;

  error = nvme_stock_ring(next_head, NVME_IO_CONSUMER(NVME_IO_CONTEXT));
  if (error) {
    error = NVME_IO_ERR_TRANSPORT;
    goto poison;
  }
  error = NVME_IO_ERR_STALE;
  __critical {
    if (nvme_io_ready()) {
      nvme_io_workspace.cq_head = next_head;
      nvme_io_workspace.cq_phase = next_phase;
      error = (status & 0x0FFEU) ? NVME_IO_ERR_STATUS : NVME_IO_OK;
    }
  }
  if (error == NVME_IO_ERR_STALE) goto poison;
  return nvme_io_workspace.last_error = error;

poison:
  REG_DMA_STATUS &= (uint8_t)~0x01;
  nvme_io_workspace.initialized = 0;
  nvme_admin_state.initialized = 0;
  return nvme_io_workspace.last_error = error;

bad_opcode:
  return nvme_io_workspace.last_error = NVME_IO_ERR_OPCODE;
}

#if MAYFLOWER_NVME_IO_TEST
#define NVME_IO_DIAG_READ  1U
#define NVME_IO_DIAG_WRITE 2U
#define NVME_IO_DIAG_FLUSH 3U

static void nvme_io_diag_schedule(uint8_t request, uint16_t lba) {
  if (nvme_io_workspace.diag_request) return;
  nvme_io_workspace.diag_lba = lba;
  nvme_io_workspace.diag_done = 0;
  nvme_io_workspace.diag_request = request;
}

static void nvme_io_diag_poll(void) __reentrant {
  uint16_t i;
  uint8_t request = nvme_io_workspace.diag_request;
  if (!request) return;
  if (!nvme_admin_state.initialized && nvme_admin_init()) {
    nvme_io_workspace.last_error = NVME_IO_ERR_ADMIN;
    goto done;
  }
  if (!nvme_io_ready() && nvme_io_init()) goto done;
  if (request == NVME_IO_DIAG_WRITE) {
    for (i = 0; i < 512; i++)
      NVME_IO_DATA_OUT[i] = (uint8_t)i ^ (uint8_t)nvme_io_workspace.diag_lba;
    nvme_io_command(NVME_IO_WRITE, nvme_io_workspace.diag_lba, 1);
  } else if (request == NVME_IO_DIAG_FLUSH) {
    nvme_io_command(NVME_IO_FLUSH, 0, 0);
  } else {
    nvme_io_command(NVME_IO_READ, nvme_io_workspace.diag_lba, 1);
  }
done:
  nvme_io_workspace.diag_request = 0;
  nvme_io_workspace.diag_done = 1;
}
#endif

#endif /* MAYFLOWER_NVME_IO_H */
