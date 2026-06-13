import json
import os
from dataclasses import dataclass
from typing import Optional

import requests

from util.KVDatabase import KVDatabase


@dataclass
class Account:
    uid: str
    name: str
    face: str
    cookies: list[dict]
    level: int = 0
    is_vip: bool = False
    coins: float = 0.0


def parse_cookie_list(cookie_str: str) -> list:
    cookies = []
    parts = cookie_str.split(",")

    merged = []
    current = ""
    for part in parts:
        if "=" in part.split(";", 1)[0]:
            if current:
                merged.append(current.strip())
            current = part
        else:
            current += "," + part
    if current:
        merged.append(current.strip())

    for item in merged:
        if ";" in item:
            key_value = item.split(";", 1)[0]
        else:
            key_value = item
        if "=" in key_value:
            key, value = key_value.split("=", 1)
            cookies.append({"name": key.strip(), "value": value.strip()})
    return cookies


def coerce_cookie_store(raw):
    if raw is None:
        return None
    if isinstance(raw, list):
        if all(isinstance(item, dict) and item.get("name") for item in raw):
            return raw
        return None
    if isinstance(raw, dict):
        cookie_value = raw.get("cookie")
        if isinstance(cookie_value, list):
            return coerce_cookie_store(cookie_value)
        default_group = raw.get("_default")
        if isinstance(default_group, dict):
            for item in default_group.values():
                if isinstance(item, dict) and item.get("key") == "cookie":
                    return coerce_cookie_store(item.get("value"))
    return None


class CookieManager:
    _COOKIE_KEY = "cookie"
    _ACCOUNTS_KEY = "accounts"

    def __init__(self, config_file_path=None, cookies=None):
        self.config_file_path = config_file_path
        self.db = KVDatabase(config_file_path)
        if cookies is not None:
            self.db.insert(self._COOKIE_KEY, cookies)

    def _load_raw_cookie_store(self):
        if not self.config_file_path or not os.path.exists(self.config_file_path):
            return None
        try:
            with open(self.config_file_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def get_cookies(self, force=False):
        stored = self.db.get(self._COOKIE_KEY)
        normalized = coerce_cookie_store(stored)
        if normalized:
            return normalized

        normalized = coerce_cookie_store(self._load_raw_cookie_store())
        if normalized:
            if self.config_file_path is not None:
                self.db.insert("cookie", normalized)
            return normalized

        if force:
            return stored
        raise RuntimeError("当前未登录，请登录")

    def have_cookies(self):
        if self.db.contains(self._COOKIE_KEY):
            return bool(coerce_cookie_store(self.db.get(self._COOKIE_KEY)))
        return bool(coerce_cookie_store(self._load_raw_cookie_store()))

    def get_cookies_str(self):
        cookies = self.get_cookies()
        cookies_str = ""
        assert cookies
        for cookie in cookies:
            cookies_str += cookie["name"] + "=" + cookie["value"] + "; "
        return cookies_str

    def get_cookies_value(self, name):
        cookies = self.get_cookies()
        assert cookies
        for cookie in cookies:
            if cookie["name"] == name:
                return cookie["value"]
        return None

    def get_config_value(self, name, default=None):
        if self.db.contains(name):
            return self.db.get(name)
        else:
            return default

    def set_config_value(self, name, value):
        self.db.insert(name, value)

    def get_accounts(self) -> list[Account]:
        raw = self.db.get(self._ACCOUNTS_KEY)
        if not isinstance(raw, list):
            return []

        accounts = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cookies = coerce_cookie_store(item.get("cookies"))
            uid = str(item.get("uid", "") or "")
            if not uid or not cookies:
                continue
            accounts.append(
                Account(
                    uid=uid,
                    name=str(item.get("name", "") or uid),
                    face=str(item.get("face", "") or ""),
                    cookies=cookies,
                    level=int(item.get("level", 0) or 0),
                    is_vip=bool(item.get("is_vip", False)),
                    coins=float(item.get("coins", 0.0) or 0.0),
                )
            )
        return accounts

    def add_account(self, cookies: list[dict]) -> Account:
        normalized = coerce_cookie_store(cookies)
        if not normalized:
            raise ValueError("cookie list is invalid")

        user_info = self._fetch_user_info(normalized)
        account = Account(
            uid=user_info["uid"],
            name=user_info["name"],
            face=user_info["face"],
            cookies=normalized,
            level=user_info["level"],
            is_vip=user_info["is_vip"],
            coins=user_info["coins"],
        )

        accounts = [a for a in self.get_accounts() if a.uid != account.uid]
        accounts.append(account)
        self._save_accounts(accounts)
        self.db.insert(self._COOKIE_KEY, account.cookies)
        return account

    def remove_account(self, uid: str) -> None:
        self._save_accounts([a for a in self.get_accounts() if a.uid != uid])

    def find_by_uid(self, uid: str) -> Optional[Account]:
        for account in self.get_accounts():
            if account.uid == uid:
                return account
        return None

    def _save_accounts(self, accounts: list[Account]) -> None:
        self.db.insert(self._ACCOUNTS_KEY, [account.__dict__ for account in accounts])

    @staticmethod
    def _cookie_value(cookies: list[dict], name: str) -> str:
        for cookie in cookies:
            if cookie.get("name") == name:
                return str(cookie.get("value", "") or "")
        return ""

    @classmethod
    def _fetch_user_info(cls, cookies: list[dict]) -> dict:
        cookies_str = "; ".join(
            f"{cookie['name']}={cookie['value']}"
            for cookie in cookies
            if cookie.get("name") and cookie.get("value") is not None
        )
        fallback_uid = cls._cookie_value(cookies, "DedeUserID")

        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "referer": "https://show.bilibili.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "cookie": cookies_str,
        }

        try:
            response = requests.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()
            data = response.json().get("data", {}) or {}
        except Exception:
            data = {}

        uid = str(data.get("mid", "") or fallback_uid)
        if not uid:
            raise RuntimeError("无法识别账号 UID，请检查 cookies 是否有效")

        return {
            "uid": uid,
            "name": str(data.get("uname", "") or uid),
            "face": str(data.get("face", "") or ""),
            "level": int((data.get("level_info", {}) or {}).get("current_level", 0) or 0),
            "is_vip": data.get("vipStatus", 0) == 1,
            "coins": float(data.get("money", 0.0) or 0.0),
        }
