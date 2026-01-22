from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class JobRequest(_message.Message):
    __slots__ = ("job_id", "app_id", "handler_type", "params")
    class ParamsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    JOB_ID_FIELD_NUMBER: _ClassVar[int]
    APP_ID_FIELD_NUMBER: _ClassVar[int]
    HANDLER_TYPE_FIELD_NUMBER: _ClassVar[int]
    PARAMS_FIELD_NUMBER: _ClassVar[int]
    job_id: str
    app_id: str
    handler_type: str
    params: _containers.ScalarMap[str, str]
    def __init__(self, job_id: _Optional[str] = ..., app_id: _Optional[str] = ..., handler_type: _Optional[str] = ..., params: _Optional[_Mapping[str, str]] = ...) -> None: ...
