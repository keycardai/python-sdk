"""Wire vectors for the SPIFFE FetchJWTSVID codec."""

import pytest

from keycardai.oauth.server._spiffe_wire import decode_response, encode_request


def test_request_encodes_repeated_audiences_and_optional_identity():
    assert encode_request((("a", "bc"), "spiffe://td")) == (
        b"\x0a\x01a\x0a\x02bc\x12\x0bspiffe://td"
    )
    assert encode_request((("a",), None)) == b"\x0a\x01a"


def test_request_counts_utf8_bytes_in_multibyte_length():
    assert encode_request((("é" * 65,), None)) == b"\x0a\x82\x01" + "é".encode() * 65


def test_response_decodes_tokens_in_order():
    assert decode_response(b"\x0a\x03\x12\x01a\x0a\x03\x12\x01b") == ["a", "b"]
    assert decode_response(b"") == []
    assert decode_response(b"\x0a\x00") == [""]


def test_response_skips_unknown_fields_of_all_wire_types():
    response = (
        b"\x18\x96\x01"  # varint
        b"\x21abcdefgh"  # fixed64
        b"\x2a\x01x"  # length-delimited
        b"\x33\x0a\x00\x3b\x3c\x34"  # nested groups
        b"\x45abcd"  # fixed32
        b"\x0a\x0d\x0a\x01i\x12\x01a\x1a\x01h\x33\x12\x00\x34"
    )
    assert decode_response(response) == ["a"]


def test_response_decodes_multibyte_lengths():
    assert decode_response(b"\x0a\x85\x01\x12\x82\x01" + b"a" * 130) == ["a" * 130]


@pytest.mark.parametrize(
    "response",
    [
        b"\x0a\x80",
        b"\x0a" + b"\x80" * 9 + b"\x02",
        b"\x0a\x02\x12",
        b"\x0a\x03\x12\x01\xff",
        b"\x00",
        b"\x18\x80",
        b"\x21abcdefg",
        b"\x2e",
        b"\x33",
        b"\x33\x3c",
        b"\x3c",
        b"\x45abc",
    ],
)
def test_response_rejects_malformed_wire_data(response):
    with pytest.raises(ValueError):
        decode_response(response)
