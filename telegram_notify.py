"""
텔레그램 알림 전송.

봇 토큰과 chat_id 는 환경변수에서 읽는다 (코드에 직접 안 박음 = 보안).
  - TELEGRAM_BOT_TOKEN
  - TELEGRAM_CHAT_ID

로컬 테스트 시:
  export TELEGRAM_BOT_TOKEN="숫자:문자열"
  export TELEGRAM_CHAT_ID="너의chat_id"
GitHub Actions 에서는 Secrets 로 자동 주입된다.
"""

import os
import requests


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 환경변수가 없습니다. "
            "README의 셋업 가이드를 참고하세요."
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,  # 링크 미리보기 켜둠 (캠페인 썸네일 보임)
    }
    resp = requests.post(url, data=payload, timeout=20)
    if resp.status_code != 200:
        raise RuntimeError(f"텔레그램 전송 실패 {resp.status_code}: {resp.text}")
    return resp.json()


if __name__ == "__main__":
    # 단독 실행 시 연결 테스트
    send_telegram("✅ 리뷰노트 모니터 텔레그램 연결 테스트 성공")
    print("전송 완료")
