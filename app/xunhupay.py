"""虎皮椒（Xunhupay）微信支付集成"""
import os
import hashlib
import time
import uuid

import httpx

API_URL = "https://api.xunhupay.com/payment/do.html"
QUERY_URL = "https://api.xunhupay.com/payment/query.html"


def _get_config():
    appid = os.environ.get("XUNHUPAY_APPID", "")
    appsecret = os.environ.get("XUNHUPAY_APPSECRET", "")
    return appid, appsecret


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def is_configured() -> bool:
    appid, appsecret = _get_config()
    return bool(appid) and bool(appsecret)


def create_order(
    trade_order_id: str,
    total_fee: float,  # 单位：元
    title: str,
    notify_url: str,
    return_url: str,
) -> dict:
    """创建虎皮椒支付订单，返回支付二维码"""
    appid, appsecret = _get_config()
    if not appid or not appsecret:
        return {"errcode": -1, "errmsg": "虎皮椒未配置，请在 .env 中设置 XUNHUPAY_APPID 和 XUNHUPAY_APPSECRET"}

    now = str(int(time.time()))
    noncestr = uuid.uuid4().hex[:16]

    # 签名: md5(appid + trade_order_id + total_fee + time + noncestr + appsecret)
    sign_str = f"{appid}{trade_order_id}{total_fee}{now}{noncestr}{appsecret}"
    sign = _md5(sign_str)

    params = {
        "version": "1.1",
        "appid": appid,
        "trade_order_id": trade_order_id,
        "total_fee": str(total_fee),
        "title": title[:100],
        "time": now,
        "notify_url": notify_url,
        "return_url": return_url,
        "noncestr": noncestr,
        "plugin": "hermes-transcriber",
        "hash": sign,
    }

    try:
        resp = httpx.post(API_URL, data=params, timeout=30)
        data = resp.json()
        return data
    except Exception as e:
        return {"errcode": -1, "errmsg": f"请求虎皮椒失败: {e}"}


def verify_notify(data: dict) -> bool:
    """验证虎皮椒回调签名"""
    appid, appsecret = _get_config()
    if not appid or not appsecret:
        return False

    sign = data.get("sign", "")
    check_str = (
        f"{appid}"
        f"{data.get('trade_order_id', '')}"
        f"{data.get('total_fee', '')}"
        f"{data.get('time', '')}"
        f"{data.get('noncestr', '')}"
        f"{data.get('status', '')}"
        f"{appsecret}"
    )
    return _md5(check_str) == sign
