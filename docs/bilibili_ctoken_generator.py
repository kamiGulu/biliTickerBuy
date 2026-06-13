"""
B站门票 Collect 令牌格式的状态化编码器。

本模块镜像了门票前端中当前观察到的 16 字节 Collect.encode() 布局。它刻意保持收集状态显式化：调用者应该使用从真实浏览器流程中捕获的状态来初始化会话，然后在 prepare 和 createV2 阶段使用相同的会话密钥，以保持计数器和 elapsed 时间的关联性。
"""

from __future__ import annotations

import base64
import struct
import time
import uuid
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple


def _uint8(value: int | float) -> int:
    """匹配前端在值超过 255 时进行钳位后的 DataView.setUint8 行为。"""
    return max(0, min(int(value or 0), 255))


def _uint16(value: int | float) -> int:
    """匹配前端在值超过 65535 时进行钳位后的 DataView.setUint16 行为。"""
    return max(0, min(int(value or 0), 65535))


@dataclass
class CollectState:
    """
    B站门票前端 Collect 编码器使用的字段快照。

    窗口和屏幕字段是快照，不是实时探测。官方前端在 Collect 初始化时记录它们，并在下一页恢复相同收集状态时保持缓存的快照。
    """

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
    screen_height: int = 0
    screen_avail_width: int = 0


def encode_collect_token(state: CollectState) -> str:
    """
    将一个 Collect 快照编码为前端使用的令牌字符串。

    布局：
      字节 0     touch end 计数
      字节 1     scrollX
      字节 2     visible 计数
      字节 3     scrollY
      字节 4     innerWidth
      字节 5     openWindow 计数
      字节 6     innerHeight
      字节 7     outerWidth
      字节 8..9  interval 秒数，大端序 uint16
      字节 10..11 自 prepare 以来的 elapsed 秒数，大端序 uint16
      字节 12    outerHeight
      字节 13    screenX
      字节 14    screenY
      字节 15    screen.width

    JavaScript bundle 在 base64 编码之前将每个字节转换为 Uint16 码单元。下面的交错零字节保留了该浏览器行为。
    """

    raw = bytearray(16)
    raw[0] = _uint8(state.touch_end_count)
    raw[1] = _uint8(state.scroll_x)
    raw[2] = _uint8(state.visible_count)
    raw[3] = _uint8(state.scroll_y)
    raw[4] = _uint8(state.inner_width)
    raw[5] = _uint8(state.open_window_count)
    raw[6] = _uint8(state.inner_height)
    raw[7] = _uint8(state.outer_width)
    raw[8:10] = struct.pack(">H", _uint16(state.interval_seconds))
    raw[10:12] = struct.pack(">H", _uint16(state.prepare_elapsed_seconds))
    raw[12] = _uint8(state.outer_height)
    raw[13] = _uint8(state.screen_x)
    raw[14] = _uint8(state.screen_y)
    raw[15] = _uint8(state.screen_width)

    browser_binary = bytearray()
    for byte in raw:
        # Uint16Array 将字节值存储为小端序 16 位码单元。
        browser_binary.extend((byte, 0))

    return base64.b64encode(browser_binary).decode("ascii")


@dataclass
class CollectSession:
    """
    一个用于 prepare -> createV2 的订单流状态机。

    在整个订单尝试过程中保留此对象或其 `session_key`。这样可以确保在不同阶段之间保持相同的单调计数器和 prepare 时间戳。
    """

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
        """
        推进 1 秒间隔计数器，不发明亚秒级 tick。

        官方页面以 1 秒间隔递增此字段。如果 prepare 和 createV2 发生在同一秒内，此计数器可以合法地保持不变。
        """

        now = time.monotonic() if now_monotonic is None else now_monotonic
        previous = self.last_counter_sync_monotonic or now
        elapsed_whole_seconds = max(0, int(now - previous))
        if elapsed_whole_seconds:
            self.state.interval_seconds += elapsed_whole_seconds
            self.last_counter_sync_monotonic = previous + elapsed_whole_seconds

    def _sync_prepare_elapsed_seconds(self, now_monotonic: Optional[float] = None) -> None:
        """
        使用整秒推进自 prepare 以来的 elapsed 时间。

        非常快的流程可能在此处为 createV2 产生 0。这与前端一致，因为 DataView.setUint16 在编码时会截断小数秒值。
        """

        if self.prepare_started_at_monotonic is None:
            return

        now = time.monotonic() if now_monotonic is None else now_monotonic
        self.state.prepare_elapsed_seconds = max(
            0,
            int(now - self.prepare_started_at_monotonic),
        )

    def mark_touch_end(self, count: int = 1) -> None:
        self.state.touch_end_count += max(0, int(count))

    def mark_visible(self, count: int = 1) -> None:
        self.state.visible_count += max(0, int(count))

    def mark_open_window(self, count: int = 1) -> None:
        self.state.open_window_count += max(0, int(count))

    def update_scroll(self, scroll_x: int, scroll_y: int) -> None:
        self.state.scroll_x = _uint8(scroll_x)
        self.state.scroll_y = _uint8(scroll_y)

    def update_window_size(self, inner_width: int, inner_height: int, outer_width: int, outer_height: int) -> None:
        self.state.inner_width = _uint8(inner_width)
        self.state.inner_height = _uint8(inner_height)
        self.state.outer_width = _uint8(outer_width)
        self.state.outer_height = _uint8(outer_height)

    def encode_prepare_token(self, *, now_monotonic: Optional[float] = None) -> str:
        """
        生成 hot prepare 字段值：请求体中的 `token`。

        前端在进入确认订单阶段之前存储 prepare 时间戳。我们在这里执行一次并为后续的 createV2 调用保留它。
        """

        now = time.monotonic() if now_monotonic is None else now_monotonic
        self._sync_interval_seconds(now)
        self.prepare_started_at_monotonic = now
        self.state.prepare_elapsed_seconds = 0
        return encode_collect_token(self.state)

    def encode_create_ctoken(self, *, now_monotonic: Optional[float] = None) -> str:
        """
        生成 hot createV2 字段值：请求体中的 `ctoken`。

        这是对同一会话状态的第二次编码。一旦计数器或 elapsed 时间发生变化，它与 prepare 令牌不同是预期的。
        """

        now = time.monotonic() if now_monotonic is None else now_monotonic
        self._sync_interval_seconds(now)
        self._sync_prepare_elapsed_seconds(now)
        return encode_collect_token(self.state)


class CollectSessionStore:
    """
    用于 Python 自动化粘合代码的最小内存会话注册表。

    调用者可以将一个订单尝试映射到一个 `session_key`，然后在 createV2 之前或在重试后续创建请求之前恢复相同的会话。
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, CollectSession] = {}

    def prepare(self, state: CollectState, *, session_key: Optional[str] = None, now_monotonic: Optional[float] = None) -> Tuple[str, str]:
        """
        执行 prepare 阶段，创建新会话并返回 session_key 和 prepare_token。

        Args:
            state: Collect 状态快照
            session_key: 可选的会话密钥，不提供则自动生成
            now_monotonic: 可选的单调时间戳

        Returns:
            (session_key, prepare_token) 元组
        """
        session = CollectSession.from_snapshot(state, session_key=session_key, now_monotonic=now_monotonic)
        prepare_token = session.encode_prepare_token(now_monotonic=now_monotonic)
        self._sessions[session.session_key] = session
        return session.session_key, prepare_token

    def create_ctoken(
        self,
        session_key: str,
        *,
        touch_end_delta: int = 0,
        visible_delta: int = 0,
        open_window_delta: int = 0,
        scroll_x: Optional[int] = None,
        scroll_y: Optional[int] = None,
        inner_width: Optional[int] = None,
        inner_height: Optional[int] = None,
        outer_width: Optional[int] = None,
        outer_height: Optional[int] = None,
        now_monotonic: Optional[float] = None,
    ) -> str:
        """
        通过 session_key 获取会话并生成 createV2 的 ctoken。

        在生成 ctoken 之前，可以更新各种状态字段以反映用户的持续操作。

        Args:
            session_key: prepare 阶段返回的会话密钥
            touch_end_delta: touch end 计数的增量
            visible_delta: visible 计数的增量
            open_window_delta: open window 计数的增量
            scroll_x: 新的滚动 X 位置（可选）
            scroll_y: 新的滚动 Y 位置（可选）
            inner_width: 新的内部宽度（可选）
            inner_height: 新的内部高度（可选）
            outer_width: 新的外部宽度（可选）
            outer_height: 新的外部高度（可选）
            now_monotonic: 可选的单调时间戳

        Returns:
            createV2 请求体中的 ctoken

        Raises:
            KeyError: 如果 session_key 不存在
        """
        session = self._sessions[session_key]

        # 更新计数器
        if touch_end_delta:
            session.mark_touch_end(touch_end_delta)
        if visible_delta:
            session.mark_visible(visible_delta)
        if open_window_delta:
            session.mark_open_window(open_window_delta)

        # 更新滚动位置
        if scroll_x is not None or scroll_y is not None:
            current_scroll_x = scroll_x if scroll_x is not None else session.state.scroll_x
            current_scroll_y = scroll_y if scroll_y is not None else session.state.scroll_y
            session.update_scroll(current_scroll_x, current_scroll_y)

        # 更新窗口尺寸
        if any(v is not None for v in [inner_width, inner_height, outer_width, outer_height]):
            current_inner_width = inner_width if inner_width is not None else session.state.inner_width
            current_inner_height = inner_height if inner_height is not None else session.state.inner_height
            current_outer_width = outer_width if outer_width is not None else session.state.outer_width
            current_outer_height = outer_height if outer_height is not None else session.state.outer_height
            session.update_window_size(current_inner_width, current_inner_height, current_outer_width, current_outer_height)

        return session.encode_create_ctoken(now_monotonic=now_monotonic)

    def get_session(self, session_key: str) -> CollectSession:
        """
        获取会话对象（高级用法）。

        Args:
            session_key: 会话密钥

        Returns:
            CollectSession 对象
        """
        return self._sessions[session_key]

    def drop(self, session_key: str) -> None:
        """
        删除会话。

        Args:
            session_key: 要删除的会话密钥
        """
        self._sessions.pop(session_key, None)


if __name__ == "__main__":
    # 仅示例：模拟完整的订单流程
    snapshot = CollectState(
        touch_end_count=35,
        interval_seconds=186,
        inner_width=1080,
        inner_height=1920,
        outer_width=1080,
        outer_height=1920,
        screen_width=800,
    )

    store = CollectSessionStore()

    # 第一步：prepare 阶段，获取 session_key 和 prepare_token
    session_key, prepare_token = store.prepare(snapshot)
    print("session_key:", session_key)
    print("prepare body.token:", prepare_token)

    # 第二步：createV2 阶段，重复使用 session_key 获取 ctoken
    for i in range(5):
        # 模拟用户操作延迟
        time.sleep(2)

        # 模拟用户在等待期间的各种操作
        create_ctoken = store.create_ctoken(
            session_key,
            touch_end_delta=2,  # 又点击了2次
            visible_delta=1,     # 页面可见性变化1次
            scroll_x=10 + i * 5, # 滚动位置变化
            scroll_y=20 + i * 10,
        )
        print(f"createV2 #{i+1} body.ctoken:", create_ctoken)

    # 清理会话
    store.drop(session_key)

