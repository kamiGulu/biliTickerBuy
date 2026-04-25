import argparse
import json
import logging
import shutil
import signal
import smtplib
import ssl
import subprocess
import sys
import time
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import requests


BASE_URL = "https://show.bilibili.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)


@dataclass
class MailConfig:
    smtp_server: str
    smtp_port: int
    sender_email: str
    sender_auth_code: str
    receiver_emails: list[str]


class AudioPlayback:
    def __init__(self) -> None:
        self._process: subprocess.Popen[Any] | None = None

    def play(self, command: list[str]) -> None:
        self.stop()
        kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        self._process = subprocess.Popen(command, **kwargs)
        time.sleep(0.5)
        if self._process.poll() is not None:
            raise RuntimeError(f"音频播放器启动失败，退出码: {self._process.returncode}")

    def stop(self) -> None:
        if not self._process:
            return
        if self._process.poll() is None:
            try:
                self._process.terminate()
                self._process.wait(timeout=3)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=3)
                except Exception:
                    pass
        self._process = None

    def wait(self) -> None:
        if self._process and self._process.poll() is None:
            self._process.wait()


class OrderMonitor:
    def __init__(self, config_path: Path):
        self.config_path = config_path.resolve()
        self.config_dir = self.config_path.parent
        self.config = self._load_json(self.config_path)
        self.stop_requested = False

        self.poll_interval_seconds = int(self.config.get("poll_interval_seconds", 60))
        self.request_timeout_seconds = int(self.config.get("request_timeout_seconds", 10))
        self.page_size = int(self.config.get("page_size", 20))
        self.max_pages = int(self.config.get("max_pages", 3))
        self.state_file = self._resolve_path(
            self.config.get("state_file", "alert_state.json")
        )
        self.audio_file = self._resolve_optional_path(self.config.get("audio_file"))
        self.audio_player_command = self.config.get("audio_player_command") or []
        self.mail_config = self._build_mail_config(self.config.get("mail", {}))
        self.session = requests.Session()
        self.session.headers.update(self._build_headers())
        self.audio_playback = AudioPlayback()

        self._setup_logging()
        self.alerted_order_ids = self._load_alert_state()

    def _setup_logging(self) -> None:
        log_file = self._resolve_path(self.config.get("log_file", "monitor.log"))
        log_file.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(log_file, encoding="utf-8"),
            ],
        )

    def _build_mail_config(self, data: dict[str, Any]) -> MailConfig:
        return MailConfig(
            smtp_server=str(data.get("SMTP_SERVER", "")).strip(),
            smtp_port=int(data.get("SMTP_PORT", 465)),
            sender_email=str(data.get("SENDER_EMAIL", "")).strip(),
            sender_auth_code=str(data.get("SENDER_AUTH_CODE", "")).strip(),
            receiver_emails=[
                str(email).strip()
                for email in data.get("RECEIVER_EMAILS", [])
                if str(email).strip()
            ],
        )

    def _resolve_path(self, value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        return (self.config_dir / path).resolve()

    def _resolve_optional_path(self, value: Any) -> Path | None:
        if not value:
            return None
        return self._resolve_path(str(value))

    def _load_json(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _load_alert_state(self) -> set[str]:
        if not self.state_file.exists():
            return set()
        try:
            payload = self._load_json(self.state_file)
            order_ids = payload.get("alerted_order_ids", [])
            return {str(order_id) for order_id in order_ids}
        except Exception as exc:
            print(f"警告: 读取状态文件失败，将忽略旧状态: {exc}")
            return set()

    def _save_alert_state(self, order_ids: set[str]) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "alerted_order_ids": sorted(order_ids),
            "updated_at": int(time.time()),
        }
        with self.state_file.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def _load_cookie_header(self) -> str:
        raw_cookie = str(self.config.get("cookie", "")).strip()
        if raw_cookie:
            return raw_cookie

        cookies_file = self.config.get("cookies_file")
        if not cookies_file:
            raise ValueError("必须配置 cookie 或 cookies_file")

        cookie_path = self._resolve_path(str(cookies_file))
        payload = self._load_json(cookie_path)

        if isinstance(payload, list):
            return "; ".join(
                f"{item['name']}={item['value']}"
                for item in payload
                if isinstance(item, dict) and item.get("name") and "value" in item
            )

        if isinstance(payload, dict):
            if "cookies" in payload and isinstance(payload["cookies"], list):
                return "; ".join(
                    f"{item['name']}={item['value']}"
                    for item in payload["cookies"]
                    if isinstance(item, dict) and item.get("name") and "value" in item
                )

            default_section = payload.get("_default", {})
            for item in default_section.values():
                cookie_list = item.get("value")
                if item.get("key") == "cookie" and isinstance(cookie_list, list):
                    return "; ".join(
                        f"{cookie['name']}={cookie['value']}"
                        for cookie in cookie_list
                        if isinstance(cookie, dict)
                        and cookie.get("name")
                        and "value" in cookie
                    )

        raise ValueError(f"无法从 cookies 文件中解析 cookie: {cookie_path}")

    def _build_headers(self) -> dict[str, str]:
        return {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5",
            "cookie": self._load_cookie_header(),
            "priority": "u=1, i",
            "referer": "https://show.bilibili.com/orderlist",
            "sec-ch-ua": '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "user-agent": str(self.config.get("user_agent", DEFAULT_USER_AGENT)),
        }

    def _fetch_ticket_list_page(self, page: int) -> list[dict[str, Any]]:
        url = f"{BASE_URL}/api/ticket/ordercenter/ticketList"
        response = self.session.get(
            url,
            params={"page": page, "page_size": self.page_size},
            timeout=self.request_timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("errno", payload.get("code")) != 0:
            raise RuntimeError(f"获取订单列表失败: {payload}")
        order_list = (payload.get("data") or {}).get("list")
        if not isinstance(order_list, list):
            raise RuntimeError(f"订单列表格式异常: {payload}")
        return order_list

    def fetch_unpaid_orders(self) -> list[dict[str, Any]]:
        all_orders: list[dict[str, Any]] = []
        for page in range(self.max_pages):
            orders = self._fetch_ticket_list_page(page)
            all_orders.extend(orders)
            if len(orders) < self.page_size:
                break
        return [order for order in all_orders if self._is_unpaid_order(order)]

    @staticmethod
    def _is_unpaid_order(order: dict[str, Any]) -> bool:
        sub_status_name = str(order.get("sub_status_name", "") or "")
        status = int(order.get("status", -1) or -1)
        sub_status = int(order.get("sub_status", -1) or -1)
        pay_remain_time = int(order.get("pay_remain_time", 0) or 0)

        if sub_status_name == "待支付":
            return True
        if status == 1 and sub_status == 1:
            return True
        if pay_remain_time > 0:
            return True
        return False

    @staticmethod
    def _pick(order: dict[str, Any], *keys: str, default: str = "") -> str:
        for key in keys:
            value = order.get(key)
            if value not in (None, ""):
                return str(value)
        return default

    def _format_order_line(self, order: dict[str, Any]) -> str:
        order_id = self._pick(order, "order_id", "orderId", default="-")
        project_name = self._pick(
            order,
            "project_name",
            "item_name",
            "projectTitle",
            "title",
            default="未知项目",
        )
        screen_name = self._pick(order, "screen_name", "session_name", default="-")
        sku_name = self._pick(order, "sku_name", "ticket_name", default="-")
        status_name = self._pick(order, "sub_status_name", "status_name", default="-")
        pay_remain_time = int(order.get("pay_remain_time", 0) or 0)
        countdown = self._format_countdown(pay_remain_time) if pay_remain_time > 0 else "-"

        return (
            f"订单号: {order_id}\n"
            f"项目: {project_name}\n"
            f"场次: {screen_name}\n"
            f"票档: {sku_name}\n"
            f"状态: {status_name}\n"
            f"剩余支付时间: {countdown}"
        )

    @staticmethod
    def _format_countdown(seconds: int) -> str:
        total_seconds = max(0, int(seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours}小时{minutes}分{secs}秒"

    def _build_email_content(self, orders: list[dict[str, Any]]) -> tuple[str, str]:
        subject = f"[B站会员购] 检测到 {len(orders)} 个未支付订单"
        body = [
            "检测到新的未支付订单，请尽快前往 https://show.bilibili.com/orderlist 完成付款。",
            "",
        ]
        for index, order in enumerate(orders, start=1):
            body.append(f"===== 未支付订单 {index} =====")
            body.append(self._format_order_line(order))
            body.append("")
        return subject, "\n".join(body).strip()

    def send_email(self, subject: str, body: str) -> None:
        if not self.mail_config.receiver_emails:
            logging.warning("未配置收件人，跳过邮件发送")
            return

        missing = [
            name
            for name, value in [
                ("SMTP_SERVER", self.mail_config.smtp_server),
                ("SENDER_EMAIL", self.mail_config.sender_email),
                ("SENDER_AUTH_CODE", self.mail_config.sender_auth_code),
            ]
            if not value
        ]
        if missing:
            raise ValueError(f"邮件配置缺失: {', '.join(missing)}")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.mail_config.sender_email
        message["To"] = ", ".join(self.mail_config.receiver_emails)
        message.set_content(body)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(
            self.mail_config.smtp_server,
            self.mail_config.smtp_port,
            context=context,
        ) as server:
            server.login(
                self.mail_config.sender_email,
                self.mail_config.sender_auth_code,
            )
            server.send_message(message)

    def play_audio(self) -> None:
        if not self.audio_file:
            logging.info("未配置音频文件，跳过播放")
            return
        if not self.audio_file.exists():
            raise FileNotFoundError(f"音频文件不存在: {self.audio_file}")
        command = self._build_audio_command()
        if not command:
            raise RuntimeError(
                "没有可用的音频播放方式。请在配置里设置 audio_player_command，"
                "或在系统中安装 ffplay/mpv/mpg123 等播放器。"
            )
        logging.info("使用音频命令播放: %s", command[0])
        self.audio_playback.play(command)

    def stop_audio(self) -> None:
        self.audio_playback.stop()

    def _build_audio_command(self) -> list[str]:
        if self.audio_player_command:
            return [
                str(part).replace("{audio_file}", str(self.audio_file))
                for part in self.audio_player_command
            ]

        if sys.platform.startswith("win"):
            audio_uri = self.audio_file.resolve().as_uri()
            return [
                "powershell",
                "-NoProfile",
                "-STA",
                "-Command",
                (
                    "Add-Type -AssemblyName presentationCore; "
                    f"$player = New-Object System.Windows.Media.MediaPlayer; "
                    f"$player.Open([Uri]'{audio_uri}'); "
                    "$player.Volume = 1.0; "
                    "$player.Play(); "
                    "while (-not $player.NaturalDuration.HasTimeSpan) { Start-Sleep -Milliseconds 200 }; "
                    "$duration = [Math]::Ceiling($player.NaturalDuration.TimeSpan.TotalSeconds); "
                    "Start-Sleep -Seconds $duration"
                ),
            ]

        candidates = [
            ["ffplay", "-nodisp", "-autoexit", str(self.audio_file)],
            ["mpv", "--no-video", str(self.audio_file)],
            ["mpg123", str(self.audio_file)],
            ["paplay", str(self.audio_file)],
            ["aplay", str(self.audio_file)],
        ]
        for command in candidates:
            if shutil.which(command[0]):
                return command
        return []

    def alert(self, new_orders: list[dict[str, Any]]) -> None:
        subject, body = self._build_email_content(new_orders)
        email_error = None
        audio_error = None

        try:
            self.send_email(subject, body)
            logging.info("邮件通知发送成功")
        except Exception as exc:
            email_error = exc
            logging.exception("邮件通知发送失败")

        try:
            self.play_audio()
            logging.info("音频提醒已触发")
        except Exception as exc:
            audio_error = exc
            logging.exception("音频提醒触发失败")

        if email_error and audio_error:
            raise RuntimeError(
                f"邮件和音频提醒都失败了: email={email_error}; audio={audio_error}"
            )

    def run_once(self) -> None:
        unpaid_orders = self.fetch_unpaid_orders()
        current_ids = {
            self._pick(order, "order_id", "orderId", default="") for order in unpaid_orders
        }
        current_ids.discard("")

        new_orders = [
            order
            for order in unpaid_orders
            if self._pick(order, "order_id", "orderId", default="")
            not in self.alerted_order_ids
        ]

        logging.info("本轮检查完成，未支付订单数: %s", len(unpaid_orders))
        if new_orders:
            logging.warning("检测到新的未支付订单数: %s", len(new_orders))
            self.alert(new_orders)
        else:
            logging.info("没有新的未支付订单")

        self.alerted_order_ids = current_ids
        self._save_alert_state(self.alerted_order_ids)

    def run_forever(self) -> None:
        logging.info("订单监控启动，轮询间隔: %s 秒", self.poll_interval_seconds)
        while not self.stop_requested:
            started_at = time.time()
            try:
                self.run_once()
            except Exception:
                logging.exception("本轮监控执行失败")

            elapsed = time.time() - started_at
            sleep_seconds = max(1, self.poll_interval_seconds - int(elapsed))
            logging.info("等待 %s 秒后开始下一轮", sleep_seconds)
            for _ in range(sleep_seconds):
                if self.stop_requested:
                    break
                time.sleep(1)

    def stop(self, *_args: Any) -> None:
        self.stop_requested = True
        self.stop_audio()
        logging.info("收到退出信号，准备停止监控")

    def test_email(self) -> None:
        subject = "[测试] B站订单监控邮件"
        body = "这是一封测试邮件，用于验证 QQ SMTP 是否可用。"
        self.send_email(subject, body)
        logging.info("测试邮件发送成功")

    def test_audio(self) -> None:
        self.play_audio()
        logging.info("测试音频已开始播放，按 Ctrl+C 可停止音乐并退出")
        try:
            self.audio_playback.wait()
        finally:
            self.stop_audio()

    def test_notify(self) -> None:
        self.test_email()
        self.test_audio()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="B站会员购未支付订单监控")
    parser.add_argument(
        "--config",
        default="config.json",
        help="配置文件路径，默认读取当前目录下的 config.json",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只执行一轮检查后退出",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="发送一封测试邮件后退出",
    )
    parser.add_argument(
        "--test-audio",
        action="store_true",
        help="播放测试音频，按 Ctrl+C 可停止并退出",
    )
    parser.add_argument(
        "--test-notify",
        action="store_true",
        help="先发送测试邮件，再播放测试音频",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    monitor = OrderMonitor(Path(args.config))
    signal.signal(signal.SIGINT, monitor.stop)
    signal.signal(signal.SIGTERM, monitor.stop)

    if args.test_email:
        monitor.test_email()
        return 0

    if args.test_audio:
        monitor.test_audio()
        return 0

    if args.test_notify:
        monitor.test_notify()
        return 0

    if args.once:
        monitor.run_once()
        return 0

    try:
        monitor.run_forever()
        return 0
    finally:
        monitor.stop_audio()


if __name__ == "__main__":
    raise SystemExit(main())
