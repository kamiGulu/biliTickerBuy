import time
import httpx
import loguru
import requests
from util.CookieManager import CookieManager

MOBILE_USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 16; PKR110 Build/AP3A.240617.008; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/138.0.7204.179 Mobile Safari/537.36 "
    "BiliApp/8910300 mobi_app/android isNotchWindow/1 "
    "NotchHeight=47 mallVersion/8910300 mVersion/352 "
    "disable_rcmd/0 magent/BILI_H5_ANDROID_16_8.91.0_8910300"
)


class BiliRequest:
    def __init__(
        self, headers=None, cookies=None, cookies_config_path=None, proxy: str = "none"
    ):
        self.session = requests.Session()
        # self.session.verify = False  # 禁用 SSL 验证，便于抓包测试
        self.proxy_list = (
            [v.strip() for v in proxy.split(",") if len(v.strip()) != 0]
            if proxy
            else []
        )
        if len(self.proxy_list) == 0:
            raise ValueError("at least have none proxy")
        self.now_proxy_idx = 0
        self._http2_client = None
        self._http2_client_proxy = None
        self._http2_unavailable = False
        self.cookieManager = CookieManager(cookies_config_path, cookies)
        self.headers = headers or {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "content-type": "application/x-www-form-urlencoded",
            "cookie": "",
            "origin": "https://mall.bilibili.com",
            "priority": "u=1, i",
            "referer": "https://mall.bilibili.com/",
            "sec-ch-ua": '"Not)A;Brand";v="8", "Chromium";v="138", "Android WebView";v="138"',
            "sec-ch-ua-mobile": "?1",
            "sec-ch-ua-platform": '"Android"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-site",
            "user-agent": MOBILE_USER_AGENT,
            "x-requested-with": "tv.danmaku.bili",
        }
        self.request_count = 0  # 记录请求次数
        self._apply_current_proxy()

    def count_and_sleep(self, threshold=60, sleep_time=60):
        """
        当记录到一定次数就sleep
        """
        self.request_count += 1
        if self.request_count % threshold == 0:
            loguru.logger.info(f"达到 {threshold} 次请求 412，休眠 {sleep_time} 秒")
            time.sleep(sleep_time)

    def clear_request_count(self):
        self.request_count = 0

    def get(self, url, data=None, isJson=False, extra_headers=None):
        self.headers["cookie"] = self.cookieManager.get_cookies_str()
        request_headers = dict(self.headers)
        if extra_headers:
            request_headers.update(extra_headers)
        if isJson:
            request_headers["Content-Type"] = "application/json"
            response = self.session.get(
                url, json=data, headers=request_headers, timeout=10
            )
        else:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = self.session.get(
                url, params=data, headers=request_headers, timeout=10
            )
        if response.status_code == 412:
            self.count_and_sleep()
            self.switch_proxy()
            loguru.logger.warning(
                f"412风控，切换代理到 {self.proxy_list[self.now_proxy_idx]}"
            )
            return self.get(url, data, isJson, extra_headers=extra_headers)
        response.raise_for_status()
        self.clear_request_count()
        if response.json().get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return response

    def switch_proxy(self):
        self.now_proxy_idx = (self.now_proxy_idx + 1) % len(self.proxy_list)
        self.close_http2_client()
        self._apply_current_proxy()

    def _apply_current_proxy(self):
        current_proxy = self.proxy_list[self.now_proxy_idx]

        if current_proxy == "none":
            self.session.proxies = {}  # 不使用任何代理，直连
        else:
            self.session.proxies = {
                "http": current_proxy,
                "https": current_proxy,
            }

    def post(self, url, data=None, isJson=False, extra_headers=None):
        self.headers["cookie"] = self.cookieManager.get_cookies_str()
        request_headers = dict(self.headers)
        if extra_headers:
            request_headers.update(extra_headers)
        if isJson:
            request_headers["Content-Type"] = "application/json"
            response = self.session.post(
                url, json=data, headers=request_headers, timeout=10
            )
        else:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = self.session.post(url, data=data, headers=request_headers, timeout=10)
        if response.status_code == 412:
            self.count_and_sleep()
            self.switch_proxy()
            loguru.logger.warning(
                f"412风控，切换代理到 {self.proxy_list[self.now_proxy_idx]}"
            )
            return self.post(url, data, isJson, extra_headers=extra_headers)
        response.raise_for_status()
        self.clear_request_count()
        if response.json().get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return response

    def _current_proxy(self):
        if not self.proxy_list:
            return None
        current_proxy = self.proxy_list[self.now_proxy_idx]
        if current_proxy == "none":
            return None
        return current_proxy

    def close_http2_client(self):
        if self._http2_client is not None:
            self._http2_client.close()
            self._http2_client = None
            self._http2_client_proxy = None

    def _get_http2_client(self):
        if self._http2_unavailable:
            raise ImportError("HTTP/2 support is unavailable")
        current_proxy = self._current_proxy()
        if (
            self._http2_client is None
            or self._http2_client_proxy != current_proxy
            or self._http2_client.is_closed
        ):
            self.close_http2_client()
            kwargs = {
                "http2": True,
                "timeout": 10,
                "follow_redirects": False,
            }
            if current_proxy:
                kwargs["proxy"] = current_proxy
            try:
                self._http2_client = httpx.Client(**kwargs)
            except ImportError:
                self._http2_unavailable = True
                raise
            self._http2_client_proxy = current_proxy
        return self._http2_client

    def post_http2(self, url, data=None, isJson=False, extra_headers=None):
        self.headers["cookie"] = self.cookieManager.get_cookies_str()
        request_headers = dict(self.headers)
        if extra_headers:
            request_headers.update(extra_headers)
        try:
            client = self._get_http2_client()
        except ImportError as exc:
            loguru.logger.warning(f"HTTP/2不可用，回退到HTTP/1.1: {exc}")
            return self.post(url, data, isJson, extra_headers=extra_headers)

        if isJson:
            request_headers["Content-Type"] = "application/json"
            response = client.post(url, json=data, headers=request_headers)
        else:
            request_headers["Content-Type"] = "application/x-www-form-urlencoded"
            response = client.post(url, data=data, headers=request_headers)

        if response.status_code == 412:
            self.count_and_sleep()
            self.switch_proxy()
            loguru.logger.warning(
                f"412风控，切换代理到 {self.proxy_list[self.now_proxy_idx]}"
            )
            return self.post_http2(url, data, isJson, extra_headers=extra_headers)
        response.raise_for_status()
        self.clear_request_count()
        if response.json().get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return response

    def get_request_name(self):
        try:
            if not self.cookieManager.have_cookies():
                loguru.logger.warning("获取用户名失败，请重新登录")
                return "未登录"
            result = self.get("https://api.bilibili.com/x/web-interface/nav").json()
            return result["data"]["uname"]
        except Exception:
            return "未登录"
