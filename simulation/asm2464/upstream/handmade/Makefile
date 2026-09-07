# Handmade firmware Makefile

# Compiler and tools
CC = sdcc
AS = sdas8051
PYTHON ?= ../.venv/bin/python

# Compiler flags
CFLAGS = -mmcs51 --model-small --opt-code-size --peep-asm --no-peep --allow-unsafe-read
CFLAGS += --std-c99
CFLAGS += -I$(SRC_DIR)
CFLAGS += --no-xinit-opt
CFLAGS += -DMAYFLOWER_UART_DEBUG=$(UART_DEBUG)
CFLAGS += -DMAYFLOWER_PROFILE=$(PROFILE_ID)
CFLAGS += -DMAYFLOWER_DEMO_EMBEDDED_SECRET=$(EMBEDDED_SECRET)
CFLAGS += -DMAYFLOWER_NVME_IO_TEST=$(NVME_IO_TEST)
CFLAGS += $(CFLAGS_EXTRA)

UART_DEBUG ?= 0
PROFILE ?= development
EMBEDDED_SECRET ?= 0
NVME_IO_TEST ?= 0
DEMO_SECRET_HEADER ?= mayflower_demo_secret.local.h
USB4_RELEASE_MIN_FREE ?= 2048

ifeq ($(PROFILE),development)
PROFILE_ID = 1
BUILD_DIR = build
else ifeq ($(PROFILE),recovery)
PROFILE_ID = 2
BUILD_DIR = build-recovery
else ifeq ($(PROFILE),usb4-demo)
PROFILE_ID = 3
ifeq ($(EMBEDDED_SECRET),1)
BUILD_DIR = build-usb4-demo-embedded
CFLAGS += -DMAYFLOWER_DEMO_SECRET_HEADER='"$(DEMO_SECRET_HEADER)"'
ifeq ($(wildcard src/$(DEMO_SECRET_HEADER)),)
$(error Missing src/$(DEMO_SECRET_HEADER); generate the ignored local header first)
endif
else
BUILD_DIR = build-usb4-demo
endif
else
$(error Unknown PROFILE '$(PROFILE)'; use development, recovery, or usb4-demo)
endif

# Memory model settings
LDFLAGS = --code-loc 0x0000 --code-size 0x10000
# Place SDCC's auto-XDATA segment in the chip's low CM RAM. The USB4 lane-bond
# path can stall on the 0x88xx scratch area once the tunnel is active, while
# 0x0B60-0x0BFF stays valid and clear of the mailbox/FSM state.
LDFLAGS += --xram-loc 0x0B60 --xram-size 0xA0
LDFLAGS += --iram-size 0x100

# Directories
SRC_DIR = src
OBJ_DIR = $(BUILD_DIR)/obj

# Source files
CRT0_SRC = $(SRC_DIR)/crt0.s
MAIN_SRC = $(SRC_DIR)/main.c

# Object files
CRT0_OBJ = $(OBJ_DIR)/crt0.rel
MAIN_OBJ = $(OBJ_DIR)/main.rel
OBJS = $(CRT0_OBJ) $(MAIN_OBJ)

# Output files
TARGET = $(BUILD_DIR)/firmware
HEX = $(TARGET).ihx
BIN = $(TARGET).bin

.PHONY: all clean flash flash-gate wrapped uflash nflash development recovery usb4-demo usb4-demo-embedded usb4-demo-test nvme-io-test profiles FORCE

FORCE:

ifneq ($(filter 1,$(EMBEDDED_SECRET) $(NVME_IO_TEST)),)
$(MAIN_OBJ): FORCE
endif

development:
	$(MAKE) PROFILE=development all

recovery:
	$(MAKE) PROFILE=recovery all

usb4-demo:
	$(MAKE) PROFILE=usb4-demo all

usb4-demo-embedded:
	$(MAKE) PROFILE=usb4-demo EMBEDDED_SECRET=1 all

usb4-demo-test:
	$(MAKE) PROFILE=usb4-demo EMBEDDED_SECRET=1 \
		BUILD_DIR=build-usb4-demo-test \
		DEMO_SECRET_HEADER=mayflower_demo_secret.test.h all

nvme-io-test:
	$(MAKE) PROFILE=development NVME_IO_TEST=1 \
		BUILD_DIR=build-nvme-io-test all

profiles: development recovery usb4-demo

all: $(BIN)
	@echo "Built: $(BIN)"
	@ls -la $(BIN)
ifeq ($(PROFILE),usb4-demo)
	@$(PYTHON) ../tools/check_firmware_size.py $(BIN) \
		--capacity 65536 --minimum-free $(USB4_RELEASE_MIN_FREE)
endif

# Create directories
$(OBJ_DIR):
	mkdir -p $(OBJ_DIR)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

# Assemble startup code
$(CRT0_OBJ): $(CRT0_SRC) | $(OBJ_DIR)
	$(AS) -l -o -s $<
	mv $(SRC_DIR)/crt0.rel $(CRT0_OBJ)
	-mv $(SRC_DIR)/crt0.lst $(OBJ_DIR)/ 2>/dev/null || true
	-mv $(SRC_DIR)/crt0.sym $(OBJ_DIR)/ 2>/dev/null || true

# Compile main source. Header deps generated as a side effect via `sdcc -MM`
# (one extra preprocessor pass; SDCC has no -MMD/-MD).
$(MAIN_OBJ): $(MAIN_SRC) | $(OBJ_DIR)
	$(CC) $(CFLAGS) -c $< -o $@
	@$(CC) $(CFLAGS) -MM $< 2>/dev/null | sed 's|^[^:]*:|$@:|' > $(OBJ_DIR)/main.d

-include $(OBJ_DIR)/main.d

# Link object files to Intel HEX
$(HEX): $(OBJS) | $(BUILD_DIR)
	$(CC) $(CFLAGS) $(LDFLAGS) $(OBJS) -o $@

# Convert Intel HEX to binary
$(BIN): $(HEX)
	@echo "Converting HEX to BIN..."
	@if command -v objcopy > /dev/null 2>&1; then \
		objcopy -I ihex -O binary $< $@; \
	elif command -v sdobjcopy > /dev/null 2>&1; then \
		sdobjcopy -I ihex -O binary $< $@; \
	else \
		$(PYTHON) -c "import sys; \
data = bytearray(0x10000); \
for line in open('$<'): \
    if line[0] != ':': continue; \
    n = int(line[1:3], 16); \
    addr = int(line[3:7], 16); \
    t = int(line[7:9], 16); \
    if t == 0: \
        for i in range(n): \
            data[addr+i] = int(line[9+i*2:11+i*2], 16); \
open('$@', 'wb').write(bytes(data).rstrip(b'\x00'))"; \
	fi

# Add ASM2464 firmware wrapper (body_len + body + magic + checksum + crc)
$(BUILD_DIR)/firmware_wrapped.bin: $(BIN)
	@echo "Wrapping firmware with ASM2464 header..."
	@$(PYTHON) -c "\
import zlib; \
body = open('$<', 'rb').read(); \
body_len = len(body).to_bytes(4, 'little'); \
checksum = bytes([sum(body) & 0xFF]); \
crc = zlib.crc32(body).to_bytes(4, 'little'); \
magic = bytes([0xA5]); \
open('$@', 'wb').write(body_len + body + magic + checksum + crc)"
	@echo "Built: $@"
	@ls -la $@

wrapped: $(BUILD_DIR)/firmware_wrapped.bin

flash-gate: $(BUILD_DIR)/firmware_wrapped.bin
	@if [ "$(ALLOW_HARDWARE_FLASH)" != "YES" ]; then \
		echo "REFUSED: set ALLOW_HARDWARE_FLASH=YES only after the no-flash audit passes"; \
		exit 2; \
	fi
	@if [ -z "$(FLASH_APPROVED_SHA256)" ]; then \
		echo "REFUSED: set FLASH_APPROVED_SHA256 to the reviewed wrapped-image hash"; \
		exit 2; \
	fi
	@actual=$$(shasum -a 256 $(BUILD_DIR)/firmware_wrapped.bin | awk '{print $$1}'); \
	if [ "$$actual" != "$(FLASH_APPROVED_SHA256)" ]; then \
		echo "REFUSED: wrapped image hash $$actual does not match approval"; \
		exit 2; \
	fi

uflash: flash-gate
	@$(PYTHON) e4_flash.py $(BUILD_DIR)/firmware_wrapped.bin

nflash: flash-gate
	@echo "Resetting to bootloader..."
	@$(PYTHON) ../ftdi_debug.py -bn
	@echo "Flashing firmware to device..."
	@$(PYTHON) ../flash.py $(BUILD_DIR)/firmware_wrapped.bin
	@$(PYTHON) ../ftdi_debug.py -rn

# Flash to device
flash: flash-gate
	@echo "Resetting to bootloader..."
	@$(PYTHON) ../ftdi_debug.py -bn
	@echo "Flashing firmware to device..."
	@$(PYTHON) ../flash.py $(BUILD_DIR)/firmware_wrapped.bin
	@echo "Resetting device..."
	@$(PYTHON) ../ftdi_debug.py -rt 2

# Clean build artifacts
clean:
	rm -rf $(BUILD_DIR)
	rm -f $(SRC_DIR)/*.asm $(SRC_DIR)/*.lst $(SRC_DIR)/*.sym
	rm -f $(SRC_DIR)/*.rel $(SRC_DIR)/*.rst $(SRC_DIR)/*.map
	rm -f *.lk *.mem *.map
