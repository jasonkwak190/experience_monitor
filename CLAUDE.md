# 리뷰노트 체험단 모니터링 봇

## 이 프로젝트가 하는 일

리뷰노트(reviewnote.co.kr) 체험단 **목록 페이지**를 주기적으로 크롤링해서,
내가 설정한 조건(지역, 카테고리, 경쟁률)에 맞는 **신규 캠페인**이 뜨면
**텔레그램으로 알림**을 보낸다.

- 상세 페이지는 긁지 않는다 (robots.txt가 막아둠 + 굳이 필요 없음)
- 알림에는 캠페인 정보 + 상세 페이지 링크만 담는다
- 링크를 받은 사용자(나)가 직접 눌러서 조건/미션을 확인하고 수동으로 신청한다

## 절대 지켜야 할 제약 (중요)

### robots.txt 준수
리뷰노트 robots.txt 내용:
```
User-Agent: *
Allow: /
Disallow: /campaigns/      ← 상세 페이지 (슬래시 뒤 ID) 크롤링 금지
Disallow: /users/
Disallow: /applies/
```

- **목록 페이지 (`/campaigns?...`)는 크롤링 허용됨** → 이것만 긁는다
- **상세 페이지 (`/campaigns/{id}`)는 절대 크롤링하지 않는다** → robots.txt 위반
- 코드 어디에서도 `/campaigns/{숫자}` 형태 URL에 자동 요청을 보내면 안 된다
- 상세 URL은 "알림에 넣을 링크"로만 사용하고, 프로그램이 직접 접속하지 않는다

### 매너 있는 크롤링
- 요청 간격을 두고, 과도한 트래픽을 만들지 않는다
- User-Agent를 정상 브라우저로 설정한다
- 1~2시간에 1회만 실행한다 (실시간 폭격 금지)

### 자동 신청 금지
- 이 봇은 **신청을 자동화하지 않는다**. 오직 알림만 보낸다.
- 신청은 사람이 직접 한다 (선정 한마디를 매번 다르게 써야 선정률이 높음 + 자동 신청은 약관 위반)

## 동작 흐름

1. 리뷰노트 목록 페이지를 여러 정렬/필터로 GET 요청
2. HTML 파싱 → 각 캠페인 카드에서 정보 추출
   - 제목, 지역, 카테고리(유형), 채널, 신청/모집 인원, 포인트, 마감일, 상세링크, 100%선정 배지 여부
3. 내 조건으로 필터링 (config.py 참고)
   - 지역이 내 동선에 포함되거나, 배송형(재택)이면 통과
   - 경쟁률 낮은 것 우선 표시
4. 이전 실행 때 본 캠페인 ID와 비교 → 신규만 추출
5. 신규 캠페인을 텔레그램으로 전송
6. 현재 본 ID 목록을 저장 (다음 실행 때 비교용)

## 환경
- Python 3.11+
- 라이브러리: requests, beautifulsoup4 (가벼움, Selenium 불필요)
- 실행 환경: GitHub Actions (1~2시간 간격, 새벽 제외 cron)
- 무료로 동작 (서버 불필요, 로컬 PC 켜둘 필요 없음)
- 로그인 불필요 (목록은 비로그인으로 다 보임)

## 파일 구조
```
reviewnote-monitor/
├── CLAUDE.md           # 이 파일 (프로젝트 맥락)
├── README.md           # 셋업 가이드
├── config.py           # 내 설정 (지역, 카테고리, 필터 조건)
├── monitor.py          # 메인 크롤러 + 필터 + 신규감지
├── telegram_notify.py  # 텔레그램 알림 전송
├── requirements.txt    # 의존성
├── seen_campaigns.json # 이미 본 캠페인 ID 저장 (자동 생성/갱신)
└── .github/
    └── workflows/
        └── monitor.yml # GitHub Actions 스케줄 설정
```

## Claude Code가 해야 할 일 (중요)

이 코드의 **HTML 파싱 부분(monitor.py의 parse_campaigns 함수)은 실제 HTML 구조에 맞게 조정이 필요**하다.
이유: 작성자가 본 페이지는 마크다운 변환본이라, 실제 div/class 셀렉터가 다를 수 있음.

**첫 실행 시 Claude Code가 할 일:**
1. `python monitor.py --debug` 실행 → 실제 받아온 HTML을 `debug_page.html`로 저장
2. 그 HTML을 열어서 캠페인 카드의 실제 구조 확인:
   - 캠페인 카드를 감싸는 태그/클래스
   - 제목, 신청/모집 숫자, 포인트, 링크가 각각 어느 태그에 있는지
3. `parse_campaigns()` 함수의 셀렉터를 실제 구조에 맞게 수정
4. `python monitor.py --test` 로 텔레그램 전송까지 확인 (신규 판정 무시하고 강제 1건 전송)
5. 정상 동작하면 GitHub에 푸시

**주의:** 리뷰노트가 Next.js 기반이라, HTML에 데이터가 안 보이고 `<script id="__NEXT_DATA__">` 안에 JSON으로 들어있을 수 있다.
그 경우 BeautifulSoup로 HTML 태그 파싱 대신, `__NEXT_DATA__` script 태그의 JSON을 파싱하는 게 훨씬 안정적이다.
parse_campaigns()에 두 방식(HTML 파싱 / NEXT_DATA JSON 파싱)을 다 시도하는 분기를 넣어뒀으니, 실제 구조 보고 맞는 쪽을 쓰면 된다.
