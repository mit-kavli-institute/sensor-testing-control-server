from __future__ import annotations

import abc


class BaseDevice(abc.ABC):
    name: str

    @abc.abstractmethod
    def connect(self) -> None: ...

    @abc.abstractmethod
    def disconnect(self) -> None: ...

    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    def status(self) -> dict: ...