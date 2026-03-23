from __future__ import annotations

import json
import re
import urllib.parse
from datetime import datetime
from typing import Any

from src.common import BiqugeError, http_get


def fundgz_fetch(fundcode: str) -> dict[str, Any]:
    """
    解析基金净值数据。
    数据源： https://fundgz.1234567.com.cn/js/<fundcode>.js
    返回字段通常包含:
    fundcode, name, jzrq, dwjz, gsz, gszzl, gztime
    """

    code = re.sub(r"\D", "", fundcode or "")
    if not re.fullmatch(r"\d{6}", code):
        raise BiqugeError(f"基金代码应为6位数字：{fundcode!r}")

    url = f"https://fundgz.1234567.com.cn/js/{code}.js"
    raw = http_get(url, headers={"Referer": "https://fundgz.1234567.com.cn/"}, timeout=20)
    text = raw.decode("utf-8", "ignore")

    m = re.search(r"jsonpgz\s*\(\s*(\{.*?\})\s*\)\s*;", text, flags=re.I | re.S)
    if not m:
        raise BiqugeError("未在 js 响应中找到 jsonpgz(...) 数据")

    payload = json.loads(m.group(1))
    if not isinstance(payload, dict):
        raise BiqugeError("jsonpgz 数据解析后非 dict")

    payload["fundcode"] = str(payload.get("fundcode") or code)
    return payload


def fund_history_fetch(fundcode: str, *, range_key: str = "y") -> list[dict[str, Any]]:
    """
    拉取基金历史净值折线数据（东财移动接口）。
    返回 Datas 列表，字段含 FSRQ / DWJZ / JZZZL 等。
    """

    code = re.sub(r"\D", "", fundcode or "")
    if not re.fullmatch(r"\d{6}", code):
        raise BiqugeError(f"基金代码应为6位数字：{fundcode!r}")

    ts = int(datetime.now().timestamp() * 1000)
    url = (
        "https://fundmobapi.eastmoney.com/FundMApi/FundNetDiagram.ashx"
        f"?FCODE={urllib.parse.quote(code)}"
        f"&RANGE={urllib.parse.quote(range_key)}"
        "&deviceid=Wap&plat=Wap&product=EFund&version=2.0.0"
        f"&_={ts}"
    )

    raw = http_get(url, headers={"Referer": "https://fund.eastmoney.com/"}, timeout=20)
    payload = json.loads(raw.decode("utf-8", "ignore"))
    if not isinstance(payload, dict):
        raise BiqugeError("历史净值接口返回非 dict")
    if int(payload.get("ErrCode") or 0) != 0:
        raise BiqugeError(f"历史净值接口错误: {payload.get('ErrCode')}")
    datas = payload.get("Datas")
    if not isinstance(datas, list):
        return []
    return [x for x in datas if isinstance(x, dict)]


__all__ = ["fundgz_fetch", "fund_history_fetch"]

