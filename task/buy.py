import json
import os
import subprocess
import sys
import threading
import time
from random import randint
from datetime import datetime
from json import JSONDecodeError
import shutil
import qrcode
import requests
from loguru import logger

from requests import HTTPError, RequestException

from util import ConfigDB, ERRNO_DICT, time_service
from util.Notifier import NotifierManager, NotifierConfig
from util.BiliRequest import BiliRequest
from util.RandomMessages import get_random_fail_message
from util.CTokenUtil import CTokenGenerator


base_url = "https://show.bilibili.com"


def _build_browser_cookie_header(_request) -> str:
    cookies = _request.cookieManager.get_cookies(force=True) or []
    return "; ".join(
        f"{cookie.get('name')}={cookie.get('value')}"
        for cookie in cookies
        if cookie.get("name") and cookie.get("value") is not None
    )


def _build_orderlist_headers(_request) -> dict:
    headers = _build_web_headers()
    headers["cookie"] = _build_browser_cookie_header(_request)
    headers["accept"] = "*/*"
    headers["referer"] = "https://show.bilibili.com/orderlist"
    headers["sec-ch-ua"] = '"Microsoft Edge";v="143", "Chromium";v="143", "Not A(Brand";v="24"'
    headers["sec-ch-ua-mobile"] = "?0"
    headers["sec-ch-ua-platform"] = '"Windows"'
    headers["sec-fetch-dest"] = "empty"
    headers["sec-fetch-mode"] = "cors"
    headers["sec-fetch-site"] = "same-origin"
    return headers


def _get_pay_param(_request, order_id) -> dict:
    url = f"{base_url}/api/ticket/order/getPayParam?order_id={order_id}"
    headers = _build_orderlist_headers(_request)

    for attempt in range(1, 3):
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data.get("errno", data.get("code")) == 0:
            return data.get("data") or {}
        if attempt < 2:
            time.sleep(1)
    raise ValueError(f"获取支付参数失败: {data}")


def get_qrcode_url(_request, order_id) -> str:
    payload = _get_pay_param(_request, order_id)
    qrcode_url = payload.get("code_url") or payload.get("codeUrl")
    if qrcode_url:
        return qrcode_url
    raise KeyError(f"code_url missing in getPayParam response: {payload}")


def _get_ticket_list(_request, *, page: int = 0, page_size: int = 10) -> list[dict]:
    url = f"{base_url}/api/ticket/ordercenter/ticketList?page={page}&page_size={page_size}"
    headers = _build_orderlist_headers(_request)
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    payload = response.json()
    if payload.get("errno", payload.get("code")) != 0:
        raise ValueError(f"获取订单列表失败: {payload}")
    data = payload.get("data") or {}
    order_list = data.get("list")
    if isinstance(order_list, list):
        return order_list
    raise ValueError(f"订单列表格式异常: {payload}")


def _is_pending_payment_order(order: dict, project_id: int, order_id: str | None = None) -> bool:
    if str(order.get("item_id", "")) != str(project_id):
        return False
    if order_id and str(order.get("order_id", "")) != str(order_id):
        return False

    sub_status_name = str(order.get("sub_status_name", "") or "")
    status = int(order.get("status", -1))
    sub_status = int(order.get("sub_status", -1))
    pay_remain_time = int(order.get("pay_remain_time", 0) or 0)

    if sub_status_name == "待支付":
        return True
    if status == 1 and sub_status == 1:
        return True
    if pay_remain_time > 0:
        return True
    return False


def _format_countdown(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}小时{minutes}分{secs}秒"


def _wait_until_start(time_start: str):
    if not time_start:
        return

    timeoffset = time_service.get_timeoffset()
    start_delay_ms = int(ConfigDB.get("go_start_delay_ms") or 50)
    yield "0) 等待开始时间"
    yield f"时间偏差已被设置为: {timeoffset}s"
    yield f"延迟抢票设置为: {start_delay_ms}ms"

    try:
        target_time = datetime.strptime(time_start, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        target_time = datetime.strptime(time_start, "%Y-%m-%dT%H:%M")

    yield f"计划抢票开始时间: {target_time.strftime('%Y-%m-%d %H:%M:%S')}"

    time_difference = (
        target_time.timestamp()
        - time.time()
        + timeoffset
        + start_delay_ms / 1000
    )
    if time_difference <= 0:
        yield "已过开抢时间，跳过等待并立即开始下单"
        return
    end_time = time.perf_counter() + time_difference
    next_report_at = float("inf")
    while True:
        remaining = end_time - time.perf_counter()
        if remaining <= 0:
            return
        if remaining <= next_report_at:
            yield f"距离开始抢票还有: {_format_countdown(remaining)}"
            next_report_at = max(0.0, remaining - 5)
        time.sleep(min(0.5, remaining))


def _build_token_payload(tickets_info: dict) -> dict:
    buyer_ids = []
    for buyer in tickets_info.get("buyer_info", []):
        buyer_id = buyer.get("id")
        if buyer_id is not None:
            buyer_ids.append(str(buyer_id))

    payload = {
        "count": tickets_info["count"],
        "screen_id": tickets_info["screen_id"],
        "order_type": 1,
        "project_id": tickets_info["project_id"],
        "sku_id": tickets_info["sku_id"],
        "buyer_info": ",".join(buyer_ids),
        "ignoreRequestLimit": True,
        "ticket_agent": "",
        "requestSource": "neul-next",
        "token": "",
        "newRisk": True,
    }
    return payload


def _build_legacy_token_payload(tickets_info: dict) -> dict:
    return {
        "count": tickets_info["count"],
        "screen_id": tickets_info["screen_id"],
        "order_type": 1,
        "project_id": tickets_info["project_id"],
        "sku_id": tickets_info["sku_id"],
        "token": "",
        "newRisk": True,
    }


def _build_compact_buyer_info(tickets_info: dict) -> list[dict]:
    buyers = []
    for buyer in tickets_info.get("buyer_info", []):
        buyers.append(
            {
                "id": buyer.get("id"),
                "name": buyer.get("name", ""),
                "tel": buyer.get("tel", ""),
                "personal_id": buyer.get("personal_id", ""),
                "id_type": buyer.get("id_type", 0),
            }
        )
    return buyers


def _build_click_position() -> dict:
    now_ms = int(time.time() * 1000)
    return {
        "x": 337,
        "y": 895,
        "origin": now_ms - 600000,
        "now": now_ms,
    }


def _get_cookie_value(cookie_list: list[dict], name: str, default: str = "") -> str:
    for cookie in cookie_list:
        if cookie.get("name") == name:
            return str(cookie.get("value", ""))
    return default


def _build_create_risk_header(cookie_list: list[dict]) -> str | None:
    identify = _get_cookie_value(cookie_list, "identify")
    uid = _get_cookie_value(cookie_list, "DedeUserID")
    local_buvid = (
        _get_cookie_value(cookie_list, "Buvid")
        or _get_cookie_value(cookie_list, "buvid3")
        or _get_cookie_value(cookie_list, "buvid4")
    )
    if not identify or not local_buvid:
        return None
    return (
        "appkey/1d8b6e7d45233436 brand/OnePlus "
        f"localBuvid/{local_buvid} mVersion/352 mallVersion/8910300 "
        "model/PKR110 osver/16 platform/h5 "
        f"uid/{uid} channel/1 deviceId/{local_buvid} "
        "sLocale/zh-Hans_CN cLocale/zh-Hans_CN "
        f"identify/{identify}"
    )


def _build_device_id(cookie_list: list[dict]) -> str:
    return (
        _get_cookie_value(cookie_list, "deviceFingerprint")
        or _get_cookie_value(cookie_list, "buvid_fp")
        or _get_cookie_value(cookie_list, "Buvid")
        or _get_cookie_value(cookie_list, "buvid3")
        or _get_cookie_value(cookie_list, "_uuid")
        or ""
    )


def _detect_order_mode(cookie_list: list[dict]) -> str:
    identify = _get_cookie_value(cookie_list, "identify")
    has_mobile_identity = bool(
        _get_cookie_value(cookie_list, "Buvid")
        or _get_cookie_value(cookie_list, "buvid_fp")
        or _get_cookie_value(cookie_list, "deviceFingerprint")
    )
    has_mobile_auth = bool(
        _get_cookie_value(cookie_list, "access_key")
        or _get_cookie_value(cookie_list, "bili_ticket")
    )
    if identify and has_mobile_identity and has_mobile_auth:
        return "mobile"
    return "web"


def _describe_cookie_capabilities(cookie_list: list[dict]) -> str:
    checks = [
        ("SESSDATA", bool(_get_cookie_value(cookie_list, "SESSDATA"))),
        ("bili_jct", bool(_get_cookie_value(cookie_list, "bili_jct"))),
        ("DedeUserID", bool(_get_cookie_value(cookie_list, "DedeUserID"))),
        ("Buvid", bool(_get_cookie_value(cookie_list, "Buvid"))),
        ("buvid_fp", bool(_get_cookie_value(cookie_list, "buvid_fp"))),
        ("deviceFingerprint", bool(_get_cookie_value(cookie_list, "deviceFingerprint"))),
        ("identify", bool(_get_cookie_value(cookie_list, "identify"))),
        ("access_key", bool(_get_cookie_value(cookie_list, "access_key"))),
        ("bili_ticket", bool(_get_cookie_value(cookie_list, "bili_ticket"))),
    ]
    return " | ".join(
        f"{name}={'Y' if enabled else 'N'}" for name, enabled in checks
    )


def _build_web_headers() -> dict:
    return {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6,zh-TW;q=0.5,ja;q=0.4",
        "content-type": "application/x-www-form-urlencoded",
        "cookie": "",
        "referer": "https://show.bilibili.com/",
        "priority": "u=1, i",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0"
        ),
    }


def _build_mobile_headers() -> dict:
    return {
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
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 16; PKR110 Build/AP3A.240617.008; wv) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/138.0.7204.179 Mobile Safari/537.36 "
            "BiliApp/8910300 mobi_app/android isNotchWindow/1 "
            "NotchHeight=47 mallVersion/8910300 mVersion/352 "
            "disable_rcmd/0 magent/BILI_H5_ANDROID_16_8.91.0_8910300"
        ),
        "x-requested-with": "tv.danmaku.bili",
    }


def _build_order_payload(tickets_info: dict, token: str) -> dict:
    device_id = tickets_info.get("device_id") or tickets_info.get("deviceId") or ""
    now_ms = int(time.time() * 1000)
    payload = {
        "project_id": tickets_info["project_id"],
        "screen_id": tickets_info["screen_id"],
        "count": tickets_info["count"],
        "pay_money": tickets_info["pay_money"],
        "order_type": 1,
        "timestamp": now_ms,
        "id_bind": 2,
        "need_contact": 0,
        "contactNoticeText": "",
        "is_package": 0,
        "package_num": 1,
        "contactInfo": None,
        "sku_id": tickets_info["sku_id"],
        "coupon_code": "",
        "again": 0,
        "token": token,
        "version": "1.1.0",
        "buyer_info": json.dumps(_build_compact_buyer_info(tickets_info), ensure_ascii=False),
        "clickPosition": _build_click_position(),
        "requestSource": "neul-next",
        "newRisk": True,
    }
    if device_id:
        payload["deviceId"] = device_id
    return payload


def _build_legacy_order_payload(tickets_info: dict, token: str) -> dict:
    payload = dict(tickets_info)
    payload["again"] = 1
    payload["token"] = token
    payload["timestamp"] = int(time.time()) * 1000
    payload.pop("detail", None)
    return payload


def _is_create_success(ret: dict, err: int) -> bool:
    if err in {100048, 100049}:
        return True
    resp_message = str(ret.get("msg", ret.get("message", "")) or "")
    return err == 0 and "defaultBBR" not in resp_message


def buy_stream(
    tickets_info,
    time_start,
    interval,
    notifier_config,
    https_proxys,
    show_random_message=True,
    show_qrcode=True,
):
    isRunning = True
    tickets_info = json.loads(tickets_info)
    detail = tickets_info["detail"]
    cookies = tickets_info["cookies"]
    tickets_info.pop("cookies", None)
    order_mode = _detect_order_mode(cookies)
    tickets_info["device_id"] = _build_device_id(cookies)
    if order_mode == "web":
        tickets_info["buyer_info"] = json.dumps(tickets_info["buyer_info"], ensure_ascii=False)
        tickets_info["deliver_info"] = json.dumps(
            tickets_info["deliver_info"], ensure_ascii=False
        )
    logger.info(f"使用代理：{https_proxys}")
    _request = BiliRequest(
        headers=_build_mobile_headers() if order_mode == "mobile" else _build_web_headers(),
        cookies=cookies,
        proxy=https_proxys,
    )

    is_hot_project = bool(tickets_info.get("is_hot_project", False))
    prepare_retry_interval_seconds = max(interval, 1000) / 1000
    create_retry_interval_seconds = max(interval, 300) / 1000
    token_payload = (
        _build_token_payload(tickets_info)
        if order_mode == "mobile"
        else _build_legacy_token_payload(tickets_info)
    )
    pending_order_ids: set[str] = set()
    confirm_lock = threading.Lock()
    confirm_stop_event = threading.Event()
    confirm_started = False
    confirmed_order_state = {
        "order": None,
        "error": None,
        "last_orders_count": 0,
    }

    def get_confirmed_order():
        with confirm_lock:
            return confirmed_order_state["order"]

    def ensure_confirm_worker():
        nonlocal confirm_started
        if confirm_started:
            return
        confirm_started = True

        def _worker():
            while not confirm_stop_event.is_set():
                with confirm_lock:
                    tracked_order_ids = list(pending_order_ids)
                if not tracked_order_ids:
                    time.sleep(0.2)
                    continue
                try:
                    orders = _get_ticket_list(_request, page=0, page_size=10)
                    with confirm_lock:
                        confirmed_order_state["last_orders_count"] = len(orders)
                    for order in orders:
                        for order_id in tracked_order_ids:
                            if _is_pending_payment_order(
                                order,
                                tickets_info["project_id"],
                                order_id,
                            ):
                                with confirm_lock:
                                    confirmed_order_state["order"] = order
                                confirm_stop_event.set()
                                return
                except Exception as exc:
                    with confirm_lock:
                        confirmed_order_state["error"] = exc
                time.sleep(1.0)

        threading.Thread(
            target=_worker,
            name="btb-order-confirm",
            daemon=True,
        ).start()

    yield from _wait_until_start(time_start)

    while isRunning:
        try:
            confirmed_order = get_confirmed_order()
            if confirmed_order:
                confirm_stop_event.set()
                notifierManager = NotifierManager.create_from_config(
                    config=notifier_config,
                    title="抢票成功",
                    content=f"bilibili会员购，请尽快前往订单中心付款: {detail}",
                )
                notifierManager.start_all()
                confirmed_order_id = confirmed_order.get("order_id")
                yield "3）抢票成功，后台已在订单列表确认待支付订单"
                yield f"订单号: {confirmed_order_id}"
                try:
                    qrcode_url = get_qrcode_url(_request, str(confirmed_order_id))
                    if show_qrcode:
                        qr_gen = qrcode.QRCode()
                        qr_gen.add_data(qrcode_url)
                        qr_gen.make(fit=True)
                        qr_gen_image = qr_gen.make_image()
                        qr_gen_image.show()  # type: ignore
                    else:
                        yield "PAYMENT_QR_URL={0}".format(qrcode_url)
                except Exception as qr_error:
                    logger.exception(qr_error)
                    yield f"付款二维码获取失败，但订单已确认待支付。orderId={confirmed_order_id}"
                    yield "请立即前往哔哩哔哩订单中心完成付款"
                break

            yield "1）订单准备"
            yield f"下单模式: {'移动端' if order_mode == 'mobile' else '网页端'}"
            yield f"Cookie 特征: {_describe_cookie_capabilities(cookies)}"
            if order_mode == "mobile":
                risk_header = _build_create_risk_header(cookies)
                yield "移动端扩展: x-risk-header={0}, deviceId={1}".format(
                    "Y" if risk_header else "N",
                    "Y" if tickets_info.get("device_id") else "N",
                )
            if is_hot_project:
                ctoken_generator = CTokenGenerator(time.time(), 0, randint(2000, 10000))
                token_payload["token"] = ctoken_generator.generate_ctoken(
                    is_create_v2=False
                )
            request_result_normal = _request.post(
                url=f"{base_url}/api/ticket/order/prepare?project_id={tickets_info['project_id']}",
                data=token_payload,
                isJson=True,
            )
            request_result = request_result_normal.json()
            yield f"请求头: {request_result_normal.headers} // 请求体: {request_result}"
            request_data = request_result.get("data")
            if not isinstance(request_data, dict) or not request_data.get("token"):
                err = int(request_result.get("errno", request_result.get("code", -1)))
                yield f"订单准备失败，跳过本轮创建订单: [{err}]({ERRNO_DICT.get(err, request_result.get('message', '未知错误'))})"
                time.sleep(prepare_retry_interval_seconds)
                continue
            yield "2）创建订单"
            payload = (
                _build_order_payload(tickets_info, request_data["token"])
                if order_mode == "mobile"
                else _build_legacy_order_payload(
                    tickets_info, request_data["token"]
                )
            )

            result = None
            for attempt in range(1, 61):
                if not isRunning:
                    yield "抢票结束"
                    break
                confirmed_order = get_confirmed_order()
                if confirmed_order:
                    yield "后台已确认待支付订单，停止当前提交重试"
                    break
                try:
                    url = f"{base_url}/api/ticket/order/createV2?project_id={tickets_info['project_id']}"
                    if is_hot_project:
                        payload["ctoken"] = ctoken_generator.generate_ctoken(  # type: ignore
                            is_create_v2=True
                        )
                        ptoken = request_data.get("ptoken") or ""
                        payload["ptoken"] = ptoken
                        payload["orderCreateUrl"] = (
                            "https://show.bilibili.com/api/ticket/order/createV2"
                        )
                        url += "&ptoken=" + ptoken
                    extra_headers = {}
                    risk_header = (
                        _build_create_risk_header(cookies)
                        if order_mode == "mobile"
                        else None
                    )
                    if risk_header:
                        extra_headers["x-risk-header"] = risk_header
                    ret = _request.post(
                        url=url,
                        data=payload,
                        isJson=True,
                        extra_headers=extra_headers or None,
                    ).json()
                    err = int(ret.get("errno", ret.get("code")))
                    if err == 100034:
                        yield f"更新票价为：{ret['data']['pay_money'] / 100}"
                        payload["pay_money"] = ret["data"]["pay_money"]
                    if _is_create_success(ret, err):
                        yield "请求成功，停止重试"
                        result = (ret, err)
                        break
                    if err == 100051:
                        break
                    yield f"[尝试 {attempt}/60]  [{err}]({ERRNO_DICT.get(err, '未知错误码')}) | {ret}"

                    time.sleep(create_retry_interval_seconds)

                except RequestException as e:
                    yield f"[尝试 {attempt}/60] 请求异常: {e}"
                    time.sleep(create_retry_interval_seconds)

                except Exception as e:
                    yield f"[尝试 {attempt}/60] 未知异常: {e}"
                    time.sleep(create_retry_interval_seconds)
            else:
                if show_random_message:
                    yield f"群友说👴： {get_random_fail_message()}"
                yield "重试次数过多，重新准备订单"
                time.sleep(prepare_retry_interval_seconds)
                continue
            if get_confirmed_order():
                continue
            if result is None:
                yield "token过期，需要重新准备订单"
                time.sleep(prepare_retry_interval_seconds)
                continue

            request_result, errno = result
            if errno == 0:
                order_id = (request_result.get("data") or {}).get("orderId")
                if order_id:
                    tracked_order_id = str(order_id)
                    with confirm_lock:
                        pending_order_ids.add(tracked_order_id)
                    ensure_confirm_worker()
                    yield f"创建订单成功，已转后台确认订单。orderId={tracked_order_id}"
                    yield "主流程将继续按限速策略尝试新的 prepare/createV2"
                    time.sleep(prepare_retry_interval_seconds)
                    continue
                yield "创建订单成功，但返回中未找到 orderId，继续抢票流程"
                time.sleep(prepare_retry_interval_seconds)
                continue
            if errno in {100048, 100049}:
                yield f"{request_result.get('msg', '已存在相关订单')}，停止重试"
                break
        except JSONDecodeError as e:
            yield f"配置文件格式错误: {e}"
            time.sleep(prepare_retry_interval_seconds)
        except HTTPError as e:
            logger.exception(e)
            yield f"请求错误: {e}"
            time.sleep(prepare_retry_interval_seconds)
        except Exception as e:
            logger.exception(e)
            yield f"程序异常: {repr(e)}"
            time.sleep(prepare_retry_interval_seconds)

    confirm_stop_event.set()


def buy(
    tickets_info,
    time_start,
    interval,
    audio_path,
    pushplusToken,
    serverchanKey,
    barkToken,
    https_proxys,
    serverchan3ApiUrl=None,
    ntfy_url=None,
    ntfy_username=None,
    ntfy_password=None,
    show_random_message=True,
    show_qrcode=True,
):
    # 创建NotifierConfig对象
    notifier_config = NotifierConfig(
        serverchan_key=serverchanKey,
        serverchan3_api_url=serverchan3ApiUrl,
        pushplus_token=pushplusToken,
        bark_token=barkToken,
        ntfy_url=ntfy_url,
        ntfy_username=ntfy_username,
        ntfy_password=ntfy_password,
        audio_path=audio_path,
    )

    for msg in buy_stream(
        tickets_info,
        time_start,
        interval,
        notifier_config,
        https_proxys,
        show_random_message,
        show_qrcode,
    ):
        logger.info(msg)


def buy_new_terminal(
    endpoint_url,
    tickets_info,
    time_start,
    interval,
    audio_path,
    pushplusToken,
    serverchanKey,
    barkToken,
    https_proxys,
    serverchan3ApiUrl=None,
    ntfy_url=None,
    ntfy_username=None,
    ntfy_password=None,
    show_random_message=True,
    terminal_ui="网页",
) -> subprocess.Popen:
    command = None

    # 1️⃣ PyInstaller / frozen
    if getattr(sys, "frozen", False):
        command = [sys.executable]
    else:
        # 2️⃣ 源码模式：检查「当前脚本目录」是否有 main.py
        script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_py = os.path.join(script_dir, "main.py")

        if os.path.exists(main_py):
            command = [sys.executable, main_py]
        # 3️⃣ 兜底：使用 btb（pip / pipx）
        else:
            btb_path = shutil.which("btb")
            if not btb_path:
                raise RuntimeError("Cannot find main.py or btb command")

            command = [btb_path]
    command.extend(["buy", tickets_info])
    if interval is not None:
        command.extend(["--interval", str(interval)])
    if time_start:
        command.extend(["--time_start", time_start])
    if audio_path:
        command.extend(["--audio_path", audio_path])
    if pushplusToken:
        command.extend(["--pushplusToken", pushplusToken])
    if serverchanKey:
        command.extend(["--serverchanKey", serverchanKey])
    if serverchan3ApiUrl:
        command.extend(["--serverchan3ApiUrl", serverchan3ApiUrl])
    if barkToken:
        command.extend(["--barkToken", barkToken])
    if ntfy_url:
        command.extend(["--ntfy_url", ntfy_url])
    if ntfy_username:
        command.extend(["--ntfy_username", ntfy_username])
    if ntfy_password:
        command.extend(["--ntfy_password", ntfy_password])
    if https_proxys:
        command.extend(["--https_proxys", https_proxys])
    if not show_random_message:
        command.extend(["--hide_random_message"])
    if terminal_ui == "网页":
        command.append("--web")
    command.extend(["--endpoint_url", endpoint_url])
    if terminal_ui == "网页":
        proc = subprocess.Popen(command)
    else:
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        proc = subprocess.Popen(command, **kwargs)
    return proc
