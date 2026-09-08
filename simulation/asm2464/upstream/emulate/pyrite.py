"""Stateful Pyrite 2.x model for Mayflower firmware tests."""

from dataclasses import dataclass, field

from tools.tcg_session import (
    CALL, ENDOFDATA, ENDOFSESSION, ENDLIST, ENDNAME, METHOD_GET, METHOD_SET,
    METHOD_START_SESSION, STARTLIST, STARTNAME, UID_ADMIN1, UID_LOCKING_RANGE_GLOBAL,
    UID_LOCKING_SP, UID_SM, method_call, packet, parse_packet, uint_atom,
)


PYRITE_STATUS_SUCCESS = 0x00
PYRITE_STATUS_NOT_AUTHORIZED = 0x01
PYRITE_STATUS_INVALID_PARAMETER = 0x0C


def _method_response(comid, invoking_uid, method_uid, parameters, *, hsn, tsn,
                     status=PYRITE_STATUS_SUCCESS):
  payload = method_call(invoking_uid, method_uid, parameters)
  payload += bytes((ENDOFDATA, STARTLIST)) + uint_atom(status) + bytes((0, 0, ENDLIST))
  return packet(comid, payload, hsn=hsn, tsn=tsn, end_of_data=False)


def _named_value(tokens, name):
  for index in range(len(tokens) - 3):
    if tokens[index:index + 2] == [STARTNAME, name] and tokens[index + 3] == ENDNAME:
      return tokens[index + 2]
  return None


@dataclass
class PyriteDevice:
  """Minimal owned/active Pyrite device with one authenticated session."""

  comid: int = 0x1004
  credential: bytes = bytes(range(32))
  read_lock_enabled: int = 1
  write_lock_enabled: int = 1
  read_locked: int = 1
  write_locked: int = 1
  ignore_unlock_set: bool = False
  next_tsn: int = 0x1001
  active_hsn: int = 0
  active_tsn: int = 0
  authenticated: bool = False
  pending_response: bytes = b""
  audit: list = field(default_factory=list)

  def _queue_error(self, invoking_uid, method_uid, hsn, tsn, status):
    self.pending_response = _method_response(
        self.comid, invoking_uid, method_uid, bytes((STARTLIST, ENDLIST)),
        hsn=hsn, tsn=tsn, status=status)

  def _valid_session(self, hsn, tsn):
    return (self.authenticated and hsn == self.active_hsn and
            tsn == self.active_tsn)

  def send(self, comid, data):
    if comid != self.comid:
      raise ValueError(f"unsupported Pyrite ComID 0x{comid:04X}")
    _tsn, _hsn, tokens = parse_packet(data)
    self.audit.append(tuple(tokens))
    if not tokens:
      raise ValueError("empty Pyrite request")

    if tokens[0] == ENDOFSESSION:
      if not self._valid_session(_hsn, _tsn):
        raise ValueError("EndSession used a stale or unauthenticated session")
      self.pending_response = packet(
          self.comid, bytes((ENDOFSESSION,)), hsn=_hsn, tsn=_tsn,
          end_of_data=False)
      self.active_hsn = self.active_tsn = 0
      self.authenticated = False
      return

    if len(tokens) < 4 or tokens[0] != CALL:
      raise ValueError("unsupported Pyrite request framing")
    invoking_uid, method_uid = tokens[1], tokens[2]

    if invoking_uid == UID_SM and method_uid == METHOD_START_SESSION:
      requested_hsn = int(tokens[4]) if len(tokens) > 4 else 0
      sp = tokens[5] if len(tokens) > 5 else b""
      challenge = _named_value(tokens, 0)
      authority = _named_value(tokens, 3)
      ok = (requested_hsn != 0 and sp == UID_LOCKING_SP and
            challenge == self.credential and authority == UID_ADMIN1 and
            not self.authenticated)
      if ok:
        self.active_hsn = requested_hsn
        self.active_tsn = self.next_tsn
        self.next_tsn += 1
        self.authenticated = True
      params = (bytes((STARTLIST,)) + uint_atom(requested_hsn) +
                uint_atom(self.active_tsn if ok else 0) + bytes((ENDLIST,)))
      self.pending_response = _method_response(
          self.comid, UID_SM, METHOD_START_SESSION, params, hsn=0, tsn=0,
          status=PYRITE_STATUS_SUCCESS if ok else PYRITE_STATUS_NOT_AUTHORIZED)
      return

    if invoking_uid == UID_LOCKING_RANGE_GLOBAL and method_uid == METHOD_GET:
      if not self._valid_session(_hsn, _tsn):
        self._queue_error(invoking_uid, method_uid, _hsn, _tsn,
                          PYRITE_STATUS_NOT_AUTHORIZED)
        return
      values = ((3, 0), (4, 0), (5, self.read_lock_enabled),
                (6, self.write_lock_enabled), (7, self.read_locked),
                (8, self.write_locked))
      params = bytearray((STARTLIST,))
      for column, value in values:
        params += bytes((STARTNAME,)) + uint_atom(column) + uint_atom(value)
        params += bytes((ENDNAME,))
      params += bytes((ENDLIST,))
      self.pending_response = _method_response(
          self.comid, invoking_uid, method_uid, params, hsn=_hsn, tsn=_tsn)
      return

    if invoking_uid == UID_LOCKING_RANGE_GLOBAL and method_uid == METHOD_SET:
      if not self._valid_session(_hsn, _tsn):
        self._queue_error(invoking_uid, method_uid, _hsn, _tsn,
                          PYRITE_STATUS_NOT_AUTHORIZED)
        return
      updates = {column: _named_value(tokens, column) for column in (7, 8)}
      if any(value not in (0, 1, None) for value in updates.values()) or all(
          value is None for value in updates.values()):
        self._queue_error(invoking_uid, method_uid, _hsn, _tsn,
                          PYRITE_STATUS_INVALID_PARAMETER)
        return
      if updates[7] is not None and not self.ignore_unlock_set:
        self.read_locked = updates[7]
      if updates[8] is not None and not self.ignore_unlock_set:
        self.write_locked = updates[8]
      self.pending_response = _method_response(
          self.comid, invoking_uid, method_uid, bytes((STARTLIST, ENDLIST)),
          hsn=_hsn, tsn=_tsn)
      return

    self._queue_error(invoking_uid, method_uid, _hsn, _tsn,
                      PYRITE_STATUS_INVALID_PARAMETER)

  def receive(self, comid, length):
    if comid != self.comid:
      raise ValueError(f"unsupported Pyrite ComID 0x{comid:04X}")
    if not self.pending_response:
      raise ValueError("Security Receive has no pending Pyrite response")
    if len(self.pending_response) > length:
      raise ValueError("Pyrite response exceeds Security Receive allocation")
    response = self.pending_response
    self.pending_response = b""
    return response + bytes(length - len(response))
