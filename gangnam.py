"""
강남맛집(강남맛집.net = xn--939au0g4vj8sq.net) 체험단 목록 수집/파싱.

- 데이터는 AJAX HTML 조각 endpoint 로 받는다(목록/endpoint 모두 robots 허용):
    GET /theme/go/_list_cmp_tpl.php?ca={cat}&rpage=1&row_num=28
    ca=20 (지역/방문형), ca=30 (제품/배송형), ca=40 (기자단).
  지역+제품을 다 받으려면 ca=20, ca=30 둘 다 호출해 합친다(기본).
- 응답은 <li data-product="{id}"> 카드들의 HTML fragment.
- 개별 상세(/cp/?id={id})는 자동 요청 금지 — 알림 링크로만 사용.
- 결과를 monitor.py 가 쓰는 표준 캠페인 dict 로 변환 (source/key 포함).

특이점:
- 제목 앞에 [서울 강서] 같은 지역 prefix 가 붙음 → 떼어내서 region_field 로,
  순수 제목만 title 로 둔다. prefix 없으면 region_field="".
- 배송형(ca=30 또는 유형 '배송형')은 재택 → region_field="재택/배송", is_delivery=True.
- 마감은 'N일 남음' 상대표기 → 실행시각(KST) 기준 날짜 'M/D' 로 근사 변환.
  '마감임박(하루전)'→내일, '오늘마감'→오늘, '마감'→오늘(expired).
- 혜택(dd.sub_tit)은 정량 포인트가 아니라 텍스트 → offer 로. point 는 항상 None.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

import config

SOURCE = "강남맛집"
KST = timezone(timedelta(hours=9))
BASE = "https://xn--939au0g4vj8sq.net"
# AJAX HTML 조각 endpoint (목록 데이터). 모듈 상수로 고정.
LIST_ENDPOINT = BASE + "/theme/go/_list_cmp_tpl.php"
# 기본으로 호출할 카테고리: 20=지역/방문, 30=제품/배송. (40=기자단은 기본 제외)
DEFAULT_CATEGORIES = (20, 30)
ROW_NUM = 28

# em.blog 의 class(채널 키, 머신리더블) → 한글 채널명. text("Blog")보다 class 가 안정적.
CHANNEL_MAP = {
    "blog": "블로그",
    "insta": "인스타그램",
    "instagram": "인스타그램",
    "reels": "릴스",
    "clip": "클립",
    "youtube": "유튜브",
    "shorts": "유튜브 쇼츠",
    "youtubeshorts": "유튜브 쇼츠",
    "tiktok": "틱톡",
}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect(categories=DEFAULT_CATEGORIES):
    """강남맛집 목록(ca별)을 긁어 표준 캠페인 dict 리스트로 반환.

    ca=20, ca=30 을 호출 사이 REQUEST_DELAY_SEC 쉬며 받아 합친다.
    같은 id 가 여러 ca 에 중복될 일은 없지만, 안전하게 id 기준 dedup.
    """
    results = []
    seen_ids = set()
    for i, ca in enumerate(categories):
        if i > 0:
            time.sleep(config.REQUEST_DELAY_SEC)
        html = _fetch(ca)
        for card in parse_campaigns(html, ca):
            std = _to_standard(card)
            if std and std["id"] not in seen_ids:
                seen_ids.add(std["id"])
                results.append(std)
    return results


def _fetch(ca):
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    params = {"ca": ca, "rpage": 1, "row_num": ROW_NUM}
    resp = requests.get(
        LIST_ENDPOINT,
        params=params,
        headers=headers,
        timeout=config.REQUEST_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    # 응답 헤더에 charset 이 없을 수 있어 UTF-8 로 고정(소스가 UTF-8 fragment).
    resp.encoding = "utf-8"
    return resp.text


# ──────────────────────────────────────────────
# HTML 카드 파싱
# ──────────────────────────────────────────────
def parse_campaigns(html, ca=None):
    soup = BeautifulSoup(html, "html.parser")
    return [parse_card(li, ca) for li in soup.select("li[data-product]")]


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_card(li, ca=None):
    out = {k: "없음" for k in
           ["id", "title", "region", "channel", "type", "applied",
            "capacity", "offer", "deadline"]}
    out["ca"] = ca

    # id (li[data-product])
    cid = (li.get("data-product") or "").strip()
    out["id"] = cid or "없음"

    # title: dt.tit a 텍스트. 앞 [지역] prefix 를 region 으로 분리.
    a = li.select_one("dt.tit a")
    raw_title = _txt(a)
    m = re.match(r"\[\s*([^\]]+?)\s*\]\s*(.*)", raw_title)
    if m:
        out["region"] = m.group(1).strip()
        out["title"] = (m.group(2).strip() or raw_title)
    else:
        out["region"] = ""  # 지역 prefix 없음
        out["title"] = raw_title or "없음"

    # channel: span.label em.blog — class(채널 키)로 한글 매핑, 실패 시 text.
    ch_em = li.select_one("span.label em.blog")
    out["channel"] = _channel_name(ch_em)

    # type: span.label em.type (방문형/배송형/기자단 등)
    out["type"] = _txt(li.select_one("span.label em.type")) or "없음"

    # 신청/모집: span.numb "신청 N / 모집 M"
    numb = li.select_one("span.numb")
    if numb:
        ntxt = numb.get_text(" ", strip=True)
        ma = re.search(r"신청\s*([\d,]+)", ntxt)
        if ma:
            out["applied"] = ma.group(1).replace(",", "")
        mc = re.search(r"모집\s*([\d,]+)", ntxt)
        if mc:
            out["capacity"] = mc.group(1).replace(",", "")

    # deadline: span.dday em.day_c ("6일 남음" / "마감임박(하루전)" / "오늘마감" 등)
    out["deadline"] = _txt(li.select_one("span.dday em.day_c")) or "없음"

    # offer(혜택): dd.sub_tit 텍스트 (정량 포인트 아님)
    out["offer"] = _txt(li.select_one("dd.sub_tit")) or "없음"

    return out


def _channel_name(ch_em):
    """em.blog 노드 → 한글 채널명. class 키 우선, 없으면 표시 텍스트 정규화."""
    if not ch_em:
        return "없음"
    for cls in ch_em.get("class", []):
        key = cls.strip().lower()
        if key in CHANNEL_MAP:
            return CHANNEL_MAP[key]
    txt = ch_em.get_text(strip=True)
    return CHANNEL_MAP.get(txt.lower(), txt or "없음")


# ──────────────────────────────────────────────
# 표준 형식 변환 (monitor.py 가 쓰는 형태)
# ──────────────────────────────────────────────
def _to_standard(card):
    cid = card.get("id")
    if not cid or cid == "없음":
        return None
    cid = str(cid)

    ctype = _clean(card.get("type"))
    channel = _clean(card.get("channel"))
    region = card.get("region", "")
    if region == "없음":
        region = ""

    # 배송형 판정: ca==30 또는 유형에 '배송' 포함
    is_delivery = (card.get("ca") == 30) or ("배송" in ctype)

    # region_field: 배송형이면 '재택/배송', 아니면 제목에서 뗀 [지역], 없으면 ''
    if is_delivery:
        region_field = "재택/배송"
    else:
        region_field = region or ""

    deadline, expired = _parse_dday(card.get("deadline", ""))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": card.get("title") or "",
        "region_field": region_field,
        "is_delivery": is_delivery,
        "type": ctype,
        "channel": channel,
        "applied": _int_or_none(card.get("applied")),
        "capacity": _int_or_none(card.get("capacity")),
        "point": None,  # 강남맛집 목록엔 정량 포인트 없음 (혜택은 offer 텍스트로)
        "offer": _clean(card.get("offer")),
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,  # 100% 선정 배지 없음 → 경쟁률로 판단
        "url": f"{BASE}/cp/?id={cid}",
    }


def _clean(v):
    return "" if v in ("없음", None) else v


def _int_or_none(s):
    if s in ("없음", "", None):
        return None
    m = re.search(r"\d+", str(s).replace(",", ""))
    return int(m.group()) if m else None


def _parse_dday(dday):
    """상대 마감표기 → ('M/D', expired).

    - 'N일 남음'        → 오늘(KST)+N일, expired=False
    - '마감임박(하루전)' → 내일,         expired=False
    - '오늘마감'/'오늘 마감' → 오늘,      expired=False (오늘까지 신청 가능)
    - '마감'/'종료'      → 오늘,          expired=True
    - 그 외 못 읽으면    → ('', False)
    """
    s = (dday or "").strip()
    if not s or s == "없음":
        return "", False

    today = datetime.now(KST)

    # 'N일 남음' (가장 흔함)
    m = re.search(r"(\d+)\s*일\s*남", s)
    if m:
        target = today + timedelta(days=int(m.group(1)))
        return f"{target.month}/{target.day}", False

    # 마감 하루 전(=내일 마감)
    if "하루전" in s or "하루 전" in s:
        target = today + timedelta(days=1)
        return f"{target.month}/{target.day}", False

    # 오늘 마감 (오늘까지는 신청 가능 → expired False)
    if re.search(r"오늘\s*마감", s):
        return f"{today.month}/{today.day}", False

    # 이미 마감/종료
    if re.search(r"마감|종료", s):
        return f"{today.month}/{today.day}", True

    # 숫자만이라도 있으면 남은 일수로 간주
    mnum = re.search(r"(\d+)", s)
    if mnum:
        target = today + timedelta(days=int(mnum.group(1)))
        return f"{target.month}/{target.day}", False

    return "", False
