"""Wire codec for the FetchJWTSVID messages in the SPIFFE Workload API.

https://github.com/spiffe/spiffe/blob/main/standards/workloadapi.proto
"""

from collections.abc import Iterator


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _string_field(tag: bytes, value: str) -> bytes:
    data = value.encode("utf-8")
    return tag + _varint(len(data)) + data


def encode_request(request: tuple[tuple[str, ...], str | None]) -> bytes:
    audiences, spiffe_id = request
    data = b"".join(_string_field(b"\x0a", audience) for audience in audiences)
    if spiffe_id:
        data += _string_field(b"\x12", spiffe_id)
    return data


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if offset == len(data):
            break
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            break
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
    raise ValueError("Invalid protobuf varint")


def _length_delimited_fields(data: bytes) -> Iterator[tuple[int, bytes]]:
    offset = 0
    groups: list[int] = []
    while offset < len(data):
        tag, offset = _read_varint(data, offset)
        field, wire_type = tag >> 3, tag & 7
        if not 0 < field < (1 << 29):
            raise ValueError("Invalid protobuf field number")
        if wire_type == 0:
            _, offset = _read_varint(data, offset)
            continue
        if wire_type == 3:
            groups.append(field)
            continue
        if wire_type == 4:
            if not groups or groups.pop() != field:
                raise ValueError("Unexpected protobuf end group")
            continue
        if wire_type == 2:
            size, offset = _read_varint(data, offset)
        elif wire_type == 1:
            size = 8
        elif wire_type == 5:
            size = 4
        else:
            raise ValueError("Invalid protobuf wire type")
        end = offset + size
        if end > len(data):
            raise ValueError("Truncated protobuf field")
        if wire_type == 2 and not groups:
            yield field, data[offset:end]
        offset = end
    if groups:
        raise ValueError("Unterminated protobuf group")


def decode_response(data: bytes) -> list[str]:
    tokens = []
    for field, svid in _length_delimited_fields(data):
        if field != 1:
            continue
        token = ""
        for child_field, value in _length_delimited_fields(svid):
            if child_field == 2:
                token = value.decode("utf-8")
        tokens.append(token)
    return tokens


def deserialize_response(data: bytes) -> list[str] | ValueError:
    # gRPC maps deserializer exceptions to RPC errors, which SDK callers retry.
    try:
        return decode_response(data)
    except ValueError as error:
        return error
