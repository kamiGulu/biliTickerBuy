import json
import time
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
        self.proxy_list = (
            [v.strip() for v in proxy.split(",") if len(v.strip()) != 0]
            if proxy
            else []
        )
        if len(self.proxy_list) == 0:
            raise ValueError("at least have none proxy")
        self.now_proxy_idx = 0
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
        if isJson:
            self.headers["Content-Type"] = "application/json"
            data = json.dumps(data)
        else:
            self.headers["Content-Type"] = "application/x-www-form-urlencoded"
        request_headers = dict(self.headers)
        if extra_headers:
            request_headers.update(extra_headers)
        response = self.session.get(url, data=data, headers=request_headers, timeout=10)
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
        if isJson:
            self.headers["content-type"] = "application/json"
            data = json.dumps(data)
        else:
            self.headers["content-type"] = "application/x-www-form-urlencoded"
        request_headers = dict(self.headers)
        if extra_headers:
            request_headers.update(extra_headers)
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

    def get_request_name(self):
        try:
            if not self.cookieManager.have_cookies():
                loguru.logger.warning("获取用户名失败，请重新登录")
                return "未登录"
            result = self.get("https://api.bilibili.com/x/web-interface/nav").json()
            return result["data"]["uname"]
        except Exception as e:
            return "未登录"
