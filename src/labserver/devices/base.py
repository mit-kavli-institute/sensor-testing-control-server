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


class BaseShutter(BaseDevice):
    @abc.abstractmethod
    def open(self) -> None: ...

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def get_state(self) -> bool: ...  # True=open


class BaseAmmeter(BaseDevice):
    @abc.abstractmethod
    def read_current(self) -> float: ...  # amps


class BaseFilterWheel(BaseDevice):
    slots: int = 6

    @abc.abstractmethod
    def move_to(
        self, slot: int, *, block: bool = True, timeout: float | None = 10.0
    ) -> None: ...

    @abc.abstractmethod
    def get_position(self) -> int: ...  # 1-6

    @abc.abstractmethod
    def home(self) -> None: ...
