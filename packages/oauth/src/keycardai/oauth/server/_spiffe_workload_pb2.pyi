# ruff: noqa
from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class JWTSVIDRequest(_message.Message):
    __slots__ = ("audience", "spiffe_id")
    AUDIENCE_FIELD_NUMBER: _ClassVar[int]
    SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    audience: _containers.RepeatedScalarFieldContainer[str]
    spiffe_id: str
    def __init__(self, audience: _Optional[_Iterable[str]] = ..., spiffe_id: _Optional[str] = ...) -> None: ...

class JWTSVIDResponse(_message.Message):
    __slots__ = ("svids",)
    SVIDS_FIELD_NUMBER: _ClassVar[int]
    svids: _containers.RepeatedCompositeFieldContainer[JWTSVID]
    def __init__(self, svids: _Optional[_Iterable[_Union[JWTSVID, _Mapping]]] = ...) -> None: ...

class JWTSVID(_message.Message):
    __slots__ = ("spiffe_id", "svid", "hint")
    SPIFFE_ID_FIELD_NUMBER: _ClassVar[int]
    SVID_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    spiffe_id: str
    svid: str
    hint: str
    def __init__(self, spiffe_id: _Optional[str] = ..., svid: _Optional[str] = ..., hint: _Optional[str] = ...) -> None: ...
