import base64
import random
import time
import uuid
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple


def _uint8(value: int | float) -> int:
    return max(0, min(int(value or 0), 255))


def _uint16(value: int | float) -> int:
    return max(0, min(int(value or 0), 65535))


@dataclass
class CollectState:
    touch_end_count: int = 0
    visible_count: int = 0
    open_window_count: int = 0
    interval_seconds: int = 0
    prepare_elapsed_seconds: int = 0
    scroll_x: int = 0
    scroll_y: int = 0
    inner_width: int = 0
    inner_height: int = 0
    outer_width: int = 0
    outer_height: int = 0
    screen_x: int = 0
    screen_y: int = 0
    screen_width: int = 0


def encode_collect_token(state: CollectState) -> str:
    raw = bytearray(16)
    raw[0] = _uint8(state.touch_end_count)
    raw[1] = _uint8(state.scroll_x)
    raw[2] = _uint8(state.visible_count)
    raw[3] = _uint8(state.scroll_y)
    raw[4] = _uint8(state.inner_width)
    raw[5] = _uint8(state.open_window_count)
    raw[6] = _uint8(state.inner_height)
    raw[7] = _uint8(state.outer_width)
    raw[8:10] = _uint16(state.interval_seconds).to_bytes(2, "big")
    raw[10:12] = _uint16(state.prepare_elapsed_seconds).to_bytes(2, "big")
    raw[12] = _uint8(state.outer_height)
    raw[13] = _uint8(state.screen_x)
    raw[14] = _uint8(state.screen_y)
    raw[15] = _uint8(state.screen_width)

    browser_binary = bytearray()
    for byte in raw:
        browser_binary.extend((byte, 0))
    return base64.b64encode(browser_binary).decode("ascii")


@dataclass
class CollectSession:
    session_key: str
    state: CollectState
    created_at_monotonic: float
    prepare_started_at_monotonic: Optional[float] = None
    last_counter_sync_monotonic: Optional[float] = None

    @classmethod
    def from_snapshot(
        cls,
        state: CollectState,
        *,
        session_key: Optional[str] = None,
        now_monotonic: Optional[float] = None,
    ) -> "CollectSession":
        now = time.monotonic() if now_monotonic is None else now_monotonic
        return cls(
            session_key=session_key or uuid.uuid4().hex,
            state=replace(state),
            created_at_monotonic=now,
            last_counter_sync_monotonic=now,
        )

    def _sync_interval_seconds(self, now_monotonic: Optional[float] = None) -> None:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        previous = self.last_counter_sync_monotonic or now
        elapsed_whole_seconds = max(0, int(now - previous))
        if elapsed_whole_seconds:
            self.state.interval_seconds += elapsed_whole_seconds
            self.last_counter_sync_monotonic = previous + elapsed_whole_seconds

    def _sync_prepare_elapsed_seconds(self, now_monotonic: Optional[float] = None) -> None:
        if self.prepare_started_at_monotonic is None:
            return
        now = time.monotonic() if now_monotonic is None else now_monotonic
        self.state.prepare_elapsed_seconds = max(
            0,
            int(now - self.prepare_started_at_monotonic),
        )

    def mark_touch_end(self, count: int = 1) -> None:
        self.state.touch_end_count += max(0, int(count))

    def update_scroll(self, scroll_x: int, scroll_y: int) -> None:
        self.state.scroll_x = _uint8(scroll_x)
        self.state.scroll_y = _uint8(scroll_y)

    def encode_prepare_token(self, *, now_monotonic: Optional[float] = None) -> str:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        self._sync_interval_seconds(now)
        self.prepare_started_at_monotonic = now
        self.state.prepare_elapsed_seconds = 0
        return encode_collect_token(self.state)

    def encode_create_ctoken(self, *, now_monotonic: Optional[float] = None) -> str:
        now = time.monotonic() if now_monotonic is None else now_monotonic
        self._sync_interval_seconds(now)
        self._sync_prepare_elapsed_seconds(now)
        return encode_collect_token(self.state)


class CTokenGenerator:
    def __init__(self, ticket_collection_t, time_offset, stay_time):
        self.ticket_collection_t = float(ticket_collection_t)
        self.time_offset = float(time_offset)
        self.stay_time = int(stay_time)
        self._sessions: Dict[str, CollectSession] = {}

    def _legacy_state(self) -> CollectState:
        return CollectState(
            touch_end_count=random.randint(3, 10),
            visible_count=2,
            open_window_count=0,
            interval_seconds=self.stay_time,
            prepare_elapsed_seconds=0,
            scroll_x=0,
            scroll_y=0,
            inner_width=255,
            inner_height=255,
            outer_width=255,
            outer_height=255,
            screen_x=0,
            screen_y=0,
            screen_width=255,
        )

    def _legacy_monotonic_now(self, session: CollectSession) -> float:
        wall_elapsed = time.time() + self.time_offset - self.ticket_collection_t
        return session.created_at_monotonic + max(0.0, wall_elapsed)

    def prepare(self) -> Tuple[str, str]:
        session = CollectSession.from_snapshot(
            self._legacy_state(),
        )
        now = self._legacy_monotonic_now(session)
        prepare_token = session.encode_prepare_token(now_monotonic=now)
        self._sessions[session.session_key] = session
        return session.session_key, prepare_token

    def create_ctoken(
        self,
        session_key: str,
        *,
        touch_end_delta: int = 0,
        scroll_x: Optional[int] = None,
        scroll_y: Optional[int] = None,
    ) -> str:
        session = self._sessions[session_key]

        # 旧方案 createV2 阶段的基准值，同时允许调用方继续叠加用户操作变化。
        session.state.touch_end_count = max(session.state.touch_end_count, 255)
        session.state.visible_count = max(session.state.visible_count, 2)
        session.state.open_window_count = max(session.state.open_window_count, 25)

        if touch_end_delta:
            session.mark_touch_end(touch_end_delta)
        if scroll_x is not None or scroll_y is not None:
            session.update_scroll(
                scroll_x if scroll_x is not None else session.state.scroll_x,
                scroll_y if scroll_y is not None else session.state.scroll_y,
            )
        return session.encode_create_ctoken(
            now_monotonic=self._legacy_monotonic_now(session),
        )
