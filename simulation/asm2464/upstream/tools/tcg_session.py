#!/usr/bin/env python3
"""Minimal TCG Storage packet/session codec for read-only table queries."""

import struct
import time


CALL, STARTLIST, ENDLIST, STARTNAME, ENDNAME = 0xF8, 0xF0, 0xF1, 0xF2, 0xF3
ENDOFDATA, ENDOFSESSION = 0xF9, 0xFA

UID_SM = bytes.fromhex("00000000000000FF")
UID_ADMIN_SP = bytes.fromhex("0000020500000001")
UID_LOCKING_SP = bytes.fromhex("0000020500000002")
UID_SID = bytes.fromhex("0000000900000006")
UID_ADMIN1 = bytes.fromhex("0000000900010001")
UID_C_PIN_MSID = bytes.fromhex("0000000B00008402")
UID_C_PIN_SID = bytes.fromhex("0000000B00000001")
UID_LOCKING_RANGE_GLOBAL = bytes.fromhex("0000080200000001")
METHOD_START_SESSION = bytes.fromhex("000000000000FF02")
METHOD_GET = bytes.fromhex("0000000600000016")
METHOD_SET = bytes.fromhex("0000000600000017")
METHOD_ACTIVATE = bytes.fromhex("0000000600000203")


def uint_atom(value):
  if not 0 <= value < 1 << 64:
    raise ValueError("TCG unsigned integer out of range")
  if value < 64:
    return bytes((value,))
  length = max(1, (value.bit_length() + 7) // 8)
  if length not in (1, 2, 4, 8):
    length = next(n for n in (1, 2, 4, 8) if n >= length)
  return bytes((0x80 | length,)) + value.to_bytes(length, "big")


def bytes_atom(value):
  value = bytes(value)
  if len(value) < 16:
    return bytes((0xA0 | len(value),)) + value
  if len(value) < 2048:
    return bytes((0xD0 | (len(value) >> 8), len(value) & 0xFF)) + value
  raise ValueError("TCG byte string too large")


def method_call(invoking_uid, method_uid, parameters):
  return bytes((CALL,)) + bytes_atom(invoking_uid) + bytes_atom(method_uid) + bytes(parameters)


def packet(comid, payload, hsn=0, tsn=0, end_of_data=True):
  body = bytearray(payload)
  if end_of_data:
    body += bytes((ENDOFDATA, STARTLIST, 0, 0, 0, ENDLIST))
  subpacket = struct.pack(">6sHI", bytes(6), 0, len(body)) + body
  while (20 + 24 + len(subpacket)) % 4:
    subpacket += b"\0"
  packet_header = struct.pack(">IIIHHII", tsn, hsn, 0, 0, 0, 0, len(subpacket))
  com_header = struct.pack(">I4sIII", 0, struct.pack(">H", comid) + b"\0\0", 0, 0,
                           len(packet_header) + len(subpacket))
  return com_header + packet_header + subpacket


def start_session_packet(comid, hsn=105, sp=UID_ADMIN_SP, challenge=None, authority=None):
  if (challenge is None) != (authority is None):
    raise ValueError("challenge and authority must be supplied together")
  params = bytes((STARTLIST,)) + uint_atom(hsn) + bytes_atom(sp) + uint_atom(1)
  if challenge is not None:
    params += bytes((STARTNAME, 0)) + bytes_atom(challenge) + bytes((ENDNAME,))
    params += bytes((STARTNAME, 3)) + bytes_atom(authority) + bytes((ENDNAME,))
  params += bytes((ENDLIST,))
  return packet(comid, method_call(UID_SM, METHOD_START_SESSION, params))


def get_msid_packet(comid, hsn, tsn):
  params = bytes((STARTLIST, STARTLIST, STARTNAME, 3)) + uint_atom(3) + bytes((ENDNAME,
      STARTNAME, 4)) + uint_atom(3) + bytes((ENDNAME, ENDLIST, ENDLIST))
  return packet(comid, method_call(UID_C_PIN_MSID, METHOD_GET, params), hsn=hsn, tsn=tsn)


def get_columns_packet(comid, hsn, tsn, object_uid, first, last):
  params = bytes((STARTLIST, STARTLIST, STARTNAME, 3)) + uint_atom(first) + bytes((ENDNAME,
      STARTNAME, 4)) + uint_atom(last) + bytes((ENDNAME, ENDLIST, ENDLIST))
  return packet(comid, method_call(object_uid, METHOD_GET, params), hsn=hsn, tsn=tsn)


def set_sid_pin_packet(comid, hsn, tsn, credential):
  params = bytes((STARTLIST, STARTNAME, 1, STARTLIST, STARTNAME, 3))
  params += bytes_atom(credential)
  params += bytes((ENDNAME, ENDLIST, ENDNAME, ENDLIST))
  return packet(comid, method_call(UID_C_PIN_SID, METHOD_SET, params), hsn=hsn, tsn=tsn)


def set_named_values_packet(comid, hsn, tsn, object_uid, values):
  params = bytearray((STARTLIST, STARTNAME, 1, STARTLIST))
  for column, value in values:
    params += bytes((STARTNAME,)) + uint_atom(column) + uint_atom(value) + bytes((ENDNAME,))
  params += bytes((ENDLIST, ENDNAME, ENDLIST))
  return packet(comid, method_call(object_uid, METHOD_SET, params), hsn=hsn, tsn=tsn)


def activate_locking_sp_packet(comid, hsn, tsn):
  params = bytes((STARTLIST, ENDLIST))
  return packet(comid, method_call(UID_LOCKING_SP, METHOD_ACTIVATE, params), hsn=hsn, tsn=tsn)


def end_session_packet(comid, hsn, tsn):
  return packet(comid, bytes((ENDOFSESSION,)), hsn=hsn, tsn=tsn, end_of_data=False)


def parse_packet(data):
  if len(data) < 56:
    raise ValueError("short TCG response")
  cp_length = struct.unpack_from(">I", data, 16)[0]
  tsn, hsn = struct.unpack_from(">II", data, 20)
  sub_length = struct.unpack_from(">I", data, 52)[0]
  if cp_length == 0 or sub_length == 0 or 56 + sub_length > len(data):
    raise ValueError("invalid TCG response lengths")
  return tsn, hsn, tokenize(data[56:56 + sub_length])


def method_status(tokens):
  if len(tokens) < 5 or tokens[-5] != STARTLIST or tokens[-1] != ENDLIST:
    raise ValueError("TCG response has no method status")
  return tokens[-4]


def tokenize(data):
  tokens = []
  offset = 0
  while offset < len(data):
    first = data[offset]
    offset += 1
    if first == 0:
      tokens.append(0)
    elif first < 0x40:
      tokens.append(first)
    elif 0x80 <= first <= 0x8F:
      length = first & 0x0F
      if offset + length > len(data): raise ValueError("short integer atom")
      tokens.append(int.from_bytes(data[offset:offset + length], "big"))
      offset += length
    elif 0xA0 <= first <= 0xAF:
      length = first & 0x0F
      if offset + length > len(data): raise ValueError("short byte-string atom")
      tokens.append(bytes(data[offset:offset + length]))
      offset += length
    elif 0xD0 <= first <= 0xD7:
      if offset >= len(data): raise ValueError("short medium atom")
      length = ((first & 7) << 8) | data[offset]
      offset += 1
      if offset + length > len(data): raise ValueError("short medium byte string")
      tokens.append(bytes(data[offset:offset + length]))
      offset += length
    else:
      tokens.append(first)
  return tokens


class ReadOnlyTCGSession:
  def __init__(self, nvme, comid):
    self.nvme, self.comid = nvme, comid
    self.hsn, self.tsn = 0, 0

  def exchange(self, command, length=2048):
    if len(command) > 2048:
      raise ValueError("TCG command exceeds guarded transfer limit")
    command += bytes((-len(command)) % 512)
    self.nvme.security_send(0x01, self.comid, command)
    time.sleep(0.025)
    return self.nvme.security_receive(0x01, self.comid, length)

  def start(self, sp=UID_ADMIN_SP, challenge=None, authority=None):
    command = start_session_packet(self.comid, sp=sp, challenge=challenge, authority=authority)
    _tsn, _hsn, tokens = parse_packet(self.exchange(command))
    if len(tokens) < 6 or tokens[0] != CALL or tokens[3] != STARTLIST:
      raise RuntimeError(f"unexpected StartSession response: {tokens!r}")
    status = method_status(tokens)
    if status != 0:
      raise RuntimeError(f"StartSession method status 0x{status:02X}")
    self.hsn, self.tsn = int(tokens[4]), int(tokens[5])
    if not self.hsn or not self.tsn:
      raise RuntimeError("StartSession returned zero HSN/TSN")
    return self.hsn, self.tsn, tokens

  def get_columns(self, object_uid, first, last):
    command = get_columns_packet(self.comid, self.hsn, self.tsn, object_uid, first, last)
    _tsn, _hsn, tokens = parse_packet(self.exchange(command))
    status = method_status(tokens)
    if status != 0:
      raise RuntimeError(f"GET method status 0x{status:02X}")
    return tokens

  def execute_checked(self, command, operation):
    _tsn, _hsn, tokens = parse_packet(self.exchange(command))
    status = method_status(tokens)
    if status != 0:
      raise RuntimeError(f"{operation} method status 0x{status:02X}")
    return tokens

  def get_msid(self):
    _tsn, _hsn, tokens = parse_packet(self.exchange(get_msid_packet(self.comid, self.hsn, self.tsn)))
    status = method_status(tokens)
    if status != 0:
      raise RuntimeError(f"MSID GET method status 0x{status:02X}")
    strings = [token for token in tokens if isinstance(token, bytes) and token not in
               (UID_C_PIN_MSID, METHOD_GET)]
    if not strings:
      raise RuntimeError(f"MSID missing from GET response: {tokens!r}")
    return max(strings, key=len), tokens

  def close(self):
    if self.hsn and self.tsn:
      response = self.exchange(end_session_packet(self.comid, self.hsn, self.tsn))
      self.hsn = self.tsn = 0
      return response
