import json
import os

from util.KVDatabase import KVDatabase


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
    def __init__(self, config_file_path=None, cookies=None):
        self.config_file_path = config_file_path
        self.db = KVDatabase(config_file_path)
        if cookies is not None:
            self.db.insert("cookie", cookies)

    def _load_raw_cookie_store(self):
        if not self.config_file_path or not os.path.exists(self.config_file_path):
            return None
        try:
            with open(self.config_file_path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except Exception:
            return None

    def get_cookies(self, force=False):
        stored = self.db.get("cookie")
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
        if self.db.contains("cookie"):
            return bool(coerce_cookie_store(self.db.get("cookie")))
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
