"""
체험뷰(chvu.co.kr) 체험단 목록 수집/파싱.

- JSON API(GET /v2/campaigns?category=...&page=1)만 호출 (정상 UA, 토큰/로그인 불필요).
  category 필수: newly(신규)/imminent(마감임박)/popular. 신규 감지용으로 newly + imminent 합침.
- robots: 상세(/campaign/{id})만 차단 → 자동 요청 금지. 상세는 알림 링크로만 사용.
- 응답 {"data":[...]} 의 각 항목을 monitor.py 표준 캠페인 dict 로 변환 (source/key 포함).

특이점:
- 지역: title 앞 prefix '[서울/강서]' 형태에서 파싱. 배송형(activity=="delivery")이면 '재택/배송'.
- channel 영문코드 → 한글 매핑 (blog→블로그, insta→인스타그램, reels→릴스 등).
- closeAt(epoch ms) → KST 변환 후 'M/D' + 마감 여부(expired) 판정.
- 두 category 합칠 때 campaignId 기준 중복 제거.
"""

import re
import time
from datetime import datetime, timedelta, timezone

import requests

import config

SOURCE = "체험뷰"
KST = timezone(timedelta(hours=9))
API_URL = "https://chvu.co.kr/v2/campaigns"
CATEGORIES = ["newly", "imminent"]
DETAIL_BASE = "https://chvu.co.kr/campaign"

# activity 코드 → 유형명 (purchase=구매평형: 구매 후 리뷰, 배송 아님)
ACTIVITY_TYPE = {"visit": "방문형", "delivery": "배송형", "purchase": "구매형"}

# channel 영문 코드 → 한글
CHANNEL_MAP = {
    "blog": "블로그",
    "insta": "인스타그램",
    "instagram": "인스타그램",
    "youtube": "유튜브",
    "reels": "릴스",
    "clip": "클립",
    "shorts": "유튜브 쇼츠",
}


# ──────────────────────────────────────────────
# 공개 진입점
# ──────────────────────────────────────────────
def collect():
    """체험뷰 API(newly+imminent)를 긁어 표준 캠페인 dict 리스트로 반환."""
    seen_ids = set()
    results = []
    for i, category in enumerate(CATEGORIES):
        if i > 0:
            time.sleep(config.REQUEST_DELAY_SEC)
        for item in _fetch(category):
            cid = item.get("campaignId")
            if cid is None or cid in seen_ids:
                continue
            seen_ids.add(cid)
            std = _to_standard(item)
            if std:
                results.append(std)
    return results


def _fetch(category):
    """한 category 의 1페이지를 가져와 data 리스트 반환."""
    headers = {"User-Agent": config.USER_AGENT, "Accept-Language": "ko-KR,ko;q=0.9"}
    params = {"category": category, "page": 1}
    resp = requests.get(API_URL, headers=headers, params=params,
                        timeout=config.REQUEST_TIMEOUT_SEC)
    resp.raise_for_status()
    data = resp.json().get("data")
    return data if isinstance(data, list) else []


# ──────────────────────────────────────────────
# 표준 형식 변환 (monitor.py 가 쓰는 형태)
# ──────────────────────────────────────────────
def _to_standard(item):
    cid = item.get("campaignId")
    if cid is None:
        return None
    cid = str(cid)

    title = (item.get("title") or "").strip()
    activity = item.get("activity")
    is_delivery = activity == "delivery"

    if is_delivery:
        region_field = "재택/배송"
    else:
        region_field = _parse_region(title)

    deadline, expired = _parse_close(item.get("closeAt"))

    return {
        "source": SOURCE,
        "id": cid,
        "key": f"{SOURCE}:{cid}",
        "title": title,
        "region_field": region_field,
        "is_delivery": is_delivery,
        "type": ACTIVITY_TYPE.get(activity, ""),
        "channel": _map_channel(item.get("channel")),
        "applied": _int_or_none(item.get("currentApplicants")),
        "capacity": _int_or_none(item.get("reviewerLimit")),
        "point": _int_or_none(item.get("rewardPoint")),
        "offer": (item.get("subtitle") or "").strip(),
        "deadline": deadline,
        "expired": expired,
        "status": "",
        "guaranteed": False,
        "url": f"{DETAIL_BASE}/{cid}",
    }


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _parse_region(title):
    """제목 앞 '[서울/강서] ...' → '서울 강서'. 없으면 ''."""
    m = re.match(r"\s*\[([^\[\]]+)\]", title or "")
    if not m:
        return ""
    inner = m.group(1).strip()
    parts = [p.strip() for p in inner.split("/") if p.strip()]
    return " ".join(parts)


def _map_channel(channel):
    """channel 코드 → 한글. 모르는 값은 원문 유지, 없으면 ''."""
    if not channel:
        return ""
    return CHANNEL_MAP.get(str(channel).strip().lower(), str(channel).strip())


def _int_or_none(v):
    if v is None:
        return None
    if isinstance(v, bool):  # bool 은 int 서브클래스라 방어
        return None
    if isinstance(v, int):
        return v
    m = re.search(r"-?\d+", str(v).replace(",", ""))
    return int(m.group()) if m else None


def _parse_close(close_at):
    """closeAt(epoch ms) → ('M/D', expired). 없거나 이상하면 ('', False)."""
    ms = _int_or_none(close_at)
    if ms is None:
        return "", False
    dt = datetime.fromtimestamp(ms / 1000, KST)
    expired = dt < datetime.now(KST)
    return f"{dt.month}/{dt.day}", expired
