"""사이트 헬스체크: 접속, Google 로그인 설정, 로그인 동기화(Firestore 읽기·쓰기), 무료 할당량을 점검함.

GitHub Actions(.github/workflows/site-healthcheck.yml)가 1시간마다 돌리고, 이상이 있을 때만
"healthcheck" 라벨이 붙은 GitHub 이슈를 열어 알림. 같은 이상이 계속되면 이슈를 조용히 갱신하고
(알림 없음), 이상 내용이 바뀌면 댓글로 알리고, 모두 정상으로 돌아오면 댓글을 달고 이슈를 닫음.

점검 항목
  1. 접속: 첫 화면(index.html)·sw.js·admin.html·부스컷 이미지·Firebase SDK(gstatic) 응답
     - 실패하면 30초 뒤 한 번 더 확인하고, 두 번 다 실패할 때만 이상으로 봄(순간 끊김으로 알림이 가지 않게)
  2. 배포: 사이트의 index.html 이 main 의 index.html 과 같은 코드인지(코드 지문 비교).
     main 에서 index.html 이 바뀐 지 30분이 지났는데도 다르면 Cloudflare 빌드 실패로 봄
  3. Google 로그인: Firebase 승인 도메인에 사이트 주소가 있는지, Google 로그인 제공자가 켜져 있는지
  4. 동기화: 전용 점검 계정(uid "healthcheck-bot")으로 로그인해 users/healthcheck-bot 문서에
     사이트와 같은 모양으로 쓰고 다시 읽어 비교. 로그인 안 한 상태로는 그 문서를 못 읽는지(보안 규칙)도 확인.
     실제 사용자 문서는 건드리지 않음. 한 번에 쓰기 1, 읽기 약 3 (하루 쓰기 24 / 읽기 72, 무료 한도의 0.2% 미만)
  5. 할당량: Firestore 오늘(태평양 시간 자정 = 한국 17:00 부터) 읽기·쓰기·삭제 수, Cloudflare Workers 오늘 요청 수
     - 기준(기본 70%)을 넘으면 이상으로 봄

필요한 비밀값(GitHub → Settings → Secrets and variables → Actions)
  FIREBASE_SA_KEY  Firebase 프로젝트(boothlist-4589c)의 서비스 계정 JSON 키. 4·5(Firestore) 점검에 씀.
                   할당량을 읽으려면 이 서비스 계정에 "Monitoring 뷰어" 역할이 있어야 함
  CF_API_TOKEN     Cloudflare API 토큰(Account Analytics 읽기). 5(Cloudflare) 점검에 씀
  CF_ACCOUNT_ID    Cloudflare 계정 ID
  NTFY_TOPIC       휴대폰 푸시 알림(ntfy 앱)의 구독 주제 이름. 있으면 이슈를 열 때·내용이 바뀔 때·복구될 때 휴대폰으로도 알림
  비밀값이 없으면 그 점검만 "건너뜀"으로 표시하고 알림은 보내지 않음.
설정 방법: docs/wiki/기능-사이트-헬스체크.md

사용:
  python tools/site_healthcheck.py            # 점검만 하고 결과 출력 (이상이 있으면 종료 코드 1)
  python tools/site_healthcheck.py --issue    # 결과로 GitHub 이슈 열기·갱신·닫기 (GITHUB_TOKEN, GITHUB_REPOSITORY 필요)
"""
import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
import uuid
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from code_fingerprint import fingerprint  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE = "https://furryboothlist.hwch.workers.dev/"
PROJECT_ID = "boothlist-4589c"
# index.html 의 firebaseConfig.apiKey (웹에 원래 공개되는 값)
API_KEY = "AIzaSyAoTXtmEeze9iOAEJOPAKp97H-xn5hyH6w"
FIREBASE_SDK = "https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js"
FIRESTORE_DOCS = f"https://firestore.googleapis.com/v1/projects/{PROJECT_ID}/databases/(default)/documents"
TEST_UID = "healthcheck-bot"
STORAGE_PREFIX = "furstclass"   # index.html 의 STORAGE_PREFIX (users/{uid} 문서 안 행사별 필드)

# 무료 플랜 하루 한도
FIRESTORE_LIMITS = {"read": 50000, "write": 20000, "delete": 20000}   # Spark, 태평양 시간 자정 리셋
CF_WORKERS_LIMIT = 100000                                              # Workers Free, UTC 자정 리셋
THRESHOLD = float(os.environ.get("HC_QUOTA_THRESHOLD", "0.7"))
DEPLOY_GRACE_MIN = 30
SLOW_SEC = 10

UA = {"User-Agent": "FurryBoothList-healthcheck (GitHub Actions)"}
KST = ZoneInfo("Asia/Seoul")
PACIFIC = ZoneInfo("America/Los_Angeles")

results = []   # {"group", "key", "status": ok|fail|warn|skip, "detail"}


def add(group, key, status, detail):
    results.append({"group": group, "key": key, "status": status, "detail": detail})
    mark = {"ok": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}[status]
    print(f"{mark} [{group}] {key}: {detail}", flush=True)


# ---------------------------------------------------------------- 1. 접속
def fetch(url, **kw):
    t0 = time.monotonic()
    r = requests.get(url, headers=UA, timeout=20, **kw)
    return r, time.monotonic() - t0


def check_url(name, url, must_contain=None):
    """두 번(30초 간격) 시도해 한 번이라도 정상이면 정상. 정상이면 응답을 돌려줌."""
    last = ""
    for attempt in range(2):
        if attempt:
            time.sleep(30)
        try:
            r, sec = fetch(url)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}"
                continue
            if must_contain and must_contain not in r.text:
                last = f"응답은 왔지만 내용이 이상함('{must_contain}' 없음)"
                continue
            note = f"HTTP 200, {sec:.1f}초"
            if sec > SLOW_SEC:
                add("접속", name, "warn", f"느림: {note} (기준 {SLOW_SEC}초)")
            else:
                add("접속", name, "ok", note)
            return r
        except requests.RequestException as e:
            last = f"연결 실패 ({type(e).__name__})"
    add("접속", name, "fail", f"{last} (30초 뒤 다시 해도 같음) · {url}")
    return None


def check_site():
    home = check_url("첫 화면", SITE, must_contain="const STORAGE_PREFIX =")
    check_url("오프라인용 sw.js", SITE + "sw.js")
    check_url("관리 화면 admin.html", SITE + "admin.html")
    check_url("부스컷 이미지", SITE + "booth-cuts/thumbs/A01.webp")
    check_url("Firebase SDK (gstatic)", FIREBASE_SDK)
    if home is not None:
        check_deploy(home.content.decode("utf-8", "replace"))


# ---------------------------------------------------------------- 2. 배포
def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True, check=True).stdout


def check_deploy(live_html):
    ref = os.environ.get("HC_MAIN_REF", "origin/main")
    try:
        main_html = git("show", f"{ref}:index.html")
        changed_at = int(git("log", "-1", "--format=%ct", ref, "--", "index.html").strip())
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError) as e:
        add("배포", "사이트가 main 과 같은지", "skip", f"main 기록을 못 읽음 ({e})")
        return
    live, want = fingerprint(live_html), fingerprint(main_html)
    age_min = (time.time() - changed_at) / 60
    if live == want:
        add("배포", "사이트가 main 과 같은지", "ok", f"같음 (코드 지문 {live})")
    elif age_min < DEPLOY_GRACE_MIN:
        add("배포", "사이트가 main 과 같은지", "ok",
            f"main 이 {age_min:.0f}분 전에 바뀌어 아직 배포 중일 수 있음 (사이트 {live}, main {want})")
    else:
        add("배포", "사이트가 main 과 같은지", "fail",
            f"main 의 index.html 이 {age_min:.0f}분 전에 바뀌었는데 사이트는 예전 코드임 "
            f"(사이트 {live}, main {want}). Cloudflare 빌드 실패 가능성 → Workers & Pages → furryboothlist → 배포 확인")


# ---------------------------------------------------------------- 3. Google 로그인 설정
def check_login_config():
    host = SITE.split("/")[2]
    try:
        r = requests.get(f"https://identitytoolkit.googleapis.com/v1/projects?key={API_KEY}", timeout=20)
        r.raise_for_status()
        domains = r.json().get("authorizedDomains", [])
        if host in domains:
            add("로그인", "승인 도메인", "ok", f"{host} 등록됨")
        else:
            add("로그인", "승인 도메인", "fail",
                f"Firebase 승인 도메인에 {host} 가 없어 Google 로그인이 막힘 → Authentication → 설정 → 승인된 도메인")
    except requests.RequestException as e:
        add("로그인", "승인 도메인", "fail", f"Firebase Auth 응답 없음 ({e})")
    try:
        r = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:createAuthUri?key={API_KEY}",
                          json={"providerId": "google.com", "continueUri": SITE}, timeout=20)
        if r.ok and r.json().get("authUri"):
            add("로그인", "Google 로그인 제공자", "ok", "켜져 있음")
        else:
            add("로그인", "Google 로그인 제공자", "fail", f"사용할 수 없음: {r.text[:200]}")
    except requests.RequestException as e:
        add("로그인", "Google 로그인 제공자", "fail", f"Firebase Auth 응답 없음 ({e})")


# ---------------------------------------------------------------- 4. 동기화
def load_sa():
    raw = os.environ.get("FIREBASE_SA_KEY", "").strip()
    if not raw:
        return None
    return json.loads(raw)


def custom_token(sa, uid):
    from google.auth import crypt, jwt
    now = int(time.time())
    payload = {
        "iss": sa["client_email"], "sub": sa["client_email"],
        "aud": "https://identitytoolkit.googleapis.com/google.identity.identitytoolkit.v1.IdentityToolkit",
        "iat": now, "exp": now + 3600, "uid": uid,
    }
    signer = crypt.RSASigner.from_service_account_info(sa)
    return jwt.encode(signer, payload).decode()


def check_sync(sa):
    g = "동기화"
    if sa is None:
        add(g, "로그인 동기화 쓰기·읽기", "skip", "비밀값 FIREBASE_SA_KEY 없음")
        return
    # 점검 계정으로 로그인 (사이트의 Google 로그인과 같은 Firebase Auth → ID 토큰)
    try:
        r = requests.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signInWithCustomToken?key={API_KEY}",
                          json={"token": custom_token(sa, TEST_UID), "returnSecureToken": True}, timeout=20)
        if not r.ok:
            add(g, "점검 계정 로그인", "fail", f"Firebase Auth 로그인 실패: HTTP {r.status_code} {r.text[:200]}")
            return
        id_token = r.json()["idToken"]
        add(g, "점검 계정 로그인", "ok", "Firebase Auth 로그인 됨")
    except Exception as e:  # 키 형식 오류 등
        add(g, "점검 계정 로그인", "fail", f"로그인 준비 실패 ({type(e).__name__}: {e})")
        return

    auth = {"Authorization": f"Bearer {id_token}"}
    doc_url = f"{FIRESTORE_DOCS}/users/{TEST_UID}"
    nonce = uuid.uuid4().hex
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    # 사이트 pushToCloud() 와 같은 모양: users/{uid} = { furstclass: { notes: {...}, ..., updatedAt } }
    body = {"fields": {STORAGE_PREFIX: {"mapValue": {"fields": {
        "notes": {"mapValue": {"fields": {"HEALTHCHECK": {"stringValue": nonce}}}},
        "updatedAt": {"stringValue": now},
    }}}}}
    try:
        t0 = time.monotonic()
        w = requests.patch(doc_url, json=body, headers=auth, timeout=20)
        if not w.ok:
            if w.status_code == 429 or "RESOURCE_EXHAUSTED" in w.text:
                add(g, "Firestore 쓰기", "fail",
                    "Firestore 무료 할당량 소진(429) → 한국 17:00 초기화 전까지 로그인 동기화·상품 정보 글 불러오기가 안 됨")
            else:
                add(g, "Firestore 쓰기", "fail", f"HTTP {w.status_code} {w.text[:200]}")
            return
        rd = requests.get(doc_url, headers=auth, timeout=20)
        sec = time.monotonic() - t0
        if not rd.ok:
            add(g, "Firestore 읽기", "fail", f"HTTP {rd.status_code} {rd.text[:200]}")
            return
        got = (rd.json().get("fields", {}).get(STORAGE_PREFIX, {}).get("mapValue", {}).get("fields", {})
               .get("notes", {}).get("mapValue", {}).get("fields", {}).get("HEALTHCHECK", {}).get("stringValue"))
        if got == nonce:
            add(g, "Firestore 쓰기·읽기", "ok", f"쓴 값을 그대로 읽음 ({sec:.1f}초)")
        else:
            add(g, "Firestore 쓰기·읽기", "fail", "쓴 값과 읽은 값이 다름")
    except requests.RequestException as e:
        add(g, "Firestore 쓰기·읽기", "fail", f"Firestore 응답 없음 ({e})")
        return


def check_public_firestore():
    """비밀값 없이도 하는 Firestore 점검 (읽기 2)."""
    g = "동기화"
    # 보안 규칙: 로그인 안 한 사람은 남의 users 문서를 못 읽어야 함
    try:
        anon = requests.get(f"{FIRESTORE_DOCS}/users/{TEST_UID}?key={API_KEY}", timeout=20)
        if anon.status_code == 403:
            add(g, "보안 규칙(남의 문서 막힘)", "ok", "로그인 안 한 요청은 거부됨")
        else:
            add(g, "보안 규칙(남의 문서 막힘)", "fail",
                f"로그인 없이 users 문서 요청이 HTTP {anon.status_code} → Firestore 규칙이 열려 있을 수 있음")
    except requests.RequestException as e:
        add(g, "보안 규칙(남의 문서 막힘)", "warn", f"확인 못 함 ({e})")

    # 모두가 읽는 상품 정보 글 문서 (내용 대신 updateTime 만 받음)
    try:
        bp = requests.get(f"{FIRESTORE_DOCS}/boothPosts/main?key={API_KEY}&mask.fieldPaths=hcNoField", timeout=20)
        if bp.ok:
            add(g, "상품 정보 글 문서 읽기", "ok", f"마지막 갱신 {bp.json().get('updateTime', '?')}")
        else:
            add(g, "상품 정보 글 문서 읽기", "fail", f"HTTP {bp.status_code} {bp.text[:200]}")
    except requests.RequestException as e:
        add(g, "상품 정보 글 문서 읽기", "fail", f"Firestore 응답 없음 ({e})")


# ---------------------------------------------------------------- 5. 할당량
def pct_status(used, limit):
    ratio = used / limit
    return ("warn" if ratio >= THRESHOLD else "ok"), f"{used:,} / {limit:,} ({ratio:.0%})"


def check_firestore_quota(sa):
    g = "할당량"
    if sa is None:
        add(g, "Firestore 오늘 사용량", "skip", "비밀값 FIREBASE_SA_KEY 없음")
        return
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import AuthorizedSession
        creds = service_account.Credentials.from_service_account_info(
            sa, scopes=["https://www.googleapis.com/auth/monitoring.read"])
        s = AuthorizedSession(creds)
    except Exception as e:
        add(g, "Firestore 오늘 사용량", "fail", f"서비스 계정 키를 못 읽음 ({e})")
        return
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now.astimezone(PACIFIC).replace(hour=0, minute=0, second=0).astimezone(dt.timezone.utc)
    secs = max(60, int((now - start).total_seconds()))
    reset_kst = (start + dt.timedelta(days=1)).astimezone(KST).strftime("%m-%d %H:%M")
    for kind, limit in FIRESTORE_LIMITS.items():
        params = {
            "filter": f'metric.type="firestore.googleapis.com/document/{kind}_count"',
            "interval.startTime": start.isoformat().replace("+00:00", "Z"),
            "interval.endTime": now.isoformat().replace("+00:00", "Z"),
            "aggregation.alignmentPeriod": f"{secs}s",
            "aggregation.perSeriesAligner": "ALIGN_SUM",
            "aggregation.crossSeriesReducer": "REDUCE_SUM",
        }
        name = f"Firestore {({'read': '읽기', 'write': '쓰기', 'delete': '삭제'})[kind]}"
        try:
            r = s.get(f"https://monitoring.googleapis.com/v3/projects/{PROJECT_ID}/timeSeries",
                      params=params, timeout=30)
        except requests.RequestException as e:
            add(g, name, "skip", f"Cloud Monitoring 이 잠시 응답하지 않음({type(e).__name__}), 다음 점검에서 다시 확인")
            continue
        if r.status_code == 403:
            try:
                why = r.json().get("error", {}).get("message", "")[:300]
            except ValueError:
                why = ""
            if "requires billing" in why:
                # Spark(무료) 플랜은 결제 계정이 없어 Cloud Monitoring 으로 사용량을 읽을 수 없음.
                # 이 경우 알림 대상이 아님: 한도를 다 쓰면 위 동기화 점검이 429(할당량 소진)로 실패해 그쪽으로 알림이 감.
                add(g, "Firestore 오늘 사용량", "skip",
                    "Spark(무료) 플랜이라 사용량을 읽을 수 없음(Cloud Monitoring 은 결제 계정 필요). "
                    "한도를 다 쓰면 동기화 점검이 '할당량 소진'으로 실패해 알림이 감")
                return
            if "has not been used" in why or "is disabled" in why or "SERVICE_DISABLED" in r.text:
                hint = "Cloud Monitoring API 가 꺼져 있음 → Google Cloud 콘솔에서 Cloud Monitoring API 사용 설정"
            else:
                hint = (f"사용량을 읽을 권한이 없음 → 서비스 계정 {sa.get('client_email')} 에 "
                        "'Monitoring 뷰어' 역할 추가 (바꾼 뒤 적용까지 몇 분 걸릴 수 있음)")
            add(g, name, "fail", f"{hint}. Google 응답: {why}")
            return
        if not r.ok:
            add(g, name, "fail", f"Cloud Monitoring HTTP {r.status_code} {r.text[:200]}")
            continue
        used = 0
        for ts in r.json().get("timeSeries", []):
            for p in ts.get("points", []):
                used += int(p["value"].get("int64Value", 0))
        status, text = pct_status(used, limit)
        add(g, name, status, f"오늘 {text}, 한국 {reset_kst}에 초기화")


def check_cloudflare_quota():
    g = "할당량"
    token, account = os.environ.get("CF_API_TOKEN", "").strip(), os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not token or not account:
        add(g, "Cloudflare Workers 오늘 요청", "skip", "비밀값 CF_API_TOKEN / CF_ACCOUNT_ID 없음")
        return
    if any(c.isspace() for c in token) or not re.fullmatch(r"[\w-]+", token):
        # 토큰 값이 로그에 찍히지 않게 내용은 보여 주지 않음
        add(g, "Cloudflare Workers 오늘 요청", "fail",
            f"비밀값 CF_API_TOKEN 이 토큰 하나가 아님(줄바꿈·공백·기호 포함, 길이 {len(token)}자). "
            "curl 명령이나 확인 결과(JSON) 전체가 아니라 토큰 값(영문·숫자 약 40자)만 넣어야 함")
        return
    if not re.fullmatch(r"[0-9a-f]{32}", account):
        add(g, "Cloudflare Workers 오늘 요청", "fail",
            f"비밀값 CF_ACCOUNT_ID 가 계정 ID 모양(영문 소문자·숫자 32자)이 아님(길이 {len(account)}자)")
        return
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now.replace(hour=0, minute=0, second=0)
    query = """query($acc: string!, $from: Time!, $to: Time!) {
      viewer { accounts(filter: {accountTag: $acc}) {
        workersInvocationsAdaptive(limit: 100, filter: {datetime_geq: $from, datetime_leq: $to}) {
          sum { requests errors }
          dimensions { scriptName }
        }
      } }
    }"""
    variables = {"acc": account, "from": start.isoformat().replace("+00:00", "Z"),
                 "to": now.isoformat().replace("+00:00", "Z")}
    # 사용량 통계 API 가 잠깐 느리거나 5xx 를 주는 것은 사이트 장애가 아니므로(2026-11-04 09:01 ReadTimeout 오탐)
    # 20초 뒤 한 번 더 해 보고, 그래도 안 되면 알림 없이 "건너뜀"으로만 남김. 토큰·권한 오류(4xx)는 계속 알림.
    r = data = None
    last = ""
    for attempt in range(2):
        if attempt:
            time.sleep(20)
        try:
            r = requests.post("https://api.cloudflare.com/client/v4/graphql",
                              headers={"Authorization": f"Bearer {token}"},
                              json={"query": query, "variables": variables}, timeout=30)
            if r.status_code >= 500:
                last = f"HTTP {r.status_code}"
                continue
            data = r.json()
            break
        except (requests.RequestException, ValueError) as e:
            last = type(e).__name__
    if data is None:
        add(g, "Cloudflare Workers 오늘 요청", "skip",
            f"Cloudflare 통계 API 가 잠시 응답하지 않음({last}, 두 번 시도). 사이트 접속과는 무관, 다음 점검에서 다시 확인")
        return
    if data.get("errors") or not r.ok:
        msg = "; ".join(e.get("message", "") for e in (data.get("errors") or []))[:200]
        add(g, "Cloudflare Workers 오늘 요청", "fail",
            f"Cloudflare API 오류 (토큰 권한 Account Analytics 읽기·계정 ID 확인): HTTP {r.status_code} {msg}")
        return
    rows = []
    for acc in (data.get("data") or {}).get("viewer", {}).get("accounts", []):
        rows += acc.get("workersInvocationsAdaptive", [])
    total = sum(x["sum"]["requests"] for x in rows)
    errors = sum(x["sum"]["errors"] for x in rows)
    reset_kst = (start + dt.timedelta(days=1)).astimezone(KST).strftime("%m-%d %H:%M")
    if not rows:
        add(g, "Cloudflare Workers 오늘 요청", "ok",
            "Worker 스크립트 실행 0건 (정적 파일만 제공 중이라 무료 한도에 들어가지 않음)")
        return
    status, text = pct_status(total, CF_WORKERS_LIMIT)
    extra = f", 오류 {errors:,}건" if errors else ""
    add(g, "Cloudflare Workers 오늘 요청", status, f"{text}{extra}, 한국 {reset_kst}에 초기화")


# ---------------------------------------------------------------- 사용량 (관리 화면 표시용, 알림 없음)
usage = {}   # 관리 화면 "사이트 상태"에 보여 줄 숫자들


def sa_session(sa, scope):
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(sa, scopes=[scope])
    return AuthorizedSession(creds)


def collect_auth_usage(sa):
    """Firebase 로그인 사용자 수: 전체, 최근 24시간에 사이트를 쓴 사람(토큰 갱신·로그인), 최근 24시간 신규."""
    if sa is None:
        return
    try:
        s = sa_session(sa, "https://www.googleapis.com/auth/cloud-platform")
        users, offset = [], 0
        while True:
            r = s.post(f"https://identitytoolkit.googleapis.com/v1/projects/{PROJECT_ID}/accounts:query",
                       json={"returnUserInfo": True, "limit": "500", "offset": str(offset)}, timeout=30)
            if not r.ok:
                usage["auth_error"] = f"HTTP {r.status_code}"
                return
            page = r.json().get("userInfo", [])
            users += page
            if len(page) < 500:
                break
            offset += 500
    except Exception as e:
        usage["auth_error"] = type(e).__name__
        return
    now = time.time()
    users = [u for u in users if u.get("localId") != TEST_UID]

    def last_active(u):
        t = 0
        if u.get("lastLoginAt"):
            t = int(u["lastLoginAt"]) / 1000
        if u.get("lastRefreshAt"):
            try:
                t = max(t, dt.datetime.fromisoformat(u["lastRefreshAt"].replace("Z", "+00:00")).timestamp())
            except ValueError:
                pass
        return t
    usage["auth_total"] = len(users)
    usage["auth_active_24h"] = sum(1 for u in users if now - last_active(u) < 86400)
    usage["auth_new_24h"] = sum(1 for u in users if u.get("createdAt") and now - int(u["createdAt"]) / 1000 < 86400)


def collect_cf_usage():
    """Cloudflare Web Analytics(사이트 index.html 의 스니펫) 오늘·어제(한국 날짜) 방문·페이지뷰."""
    token, account = os.environ.get("CF_API_TOKEN", "").strip(), os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not token or not account or not re.fullmatch(r"[\w-]+", token):
        return
    now = dt.datetime.now(KST).replace(microsecond=0)
    t0 = now.replace(hour=0, minute=0, second=0)
    y0 = t0 - dt.timedelta(days=1)
    z = lambda d: d.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    query = """query($acc: string!, $t0: Time!, $t1: Time!, $y0: Time!) {
      viewer { accounts(filter: {accountTag: $acc}) {
        today: rumPageloadEventsAdaptiveGroups(limit: 1, filter: {datetime_geq: $t0, datetime_leq: $t1}) { count sum { visits } }
        yesterday: rumPageloadEventsAdaptiveGroups(limit: 1, filter: {datetime_geq: $y0, datetime_lt: $t0}) { count sum { visits } }
      } }
    }"""
    try:
        r = requests.post("https://api.cloudflare.com/client/v4/graphql",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"query": query, "variables": {"acc": account, "t0": z(t0), "t1": z(now), "y0": z(y0)}},
                          timeout=30)
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        usage["web_error"] = type(e).__name__
        return
    if data.get("errors"):
        usage["web_error"] = "; ".join(e.get("message", "") for e in data["errors"])[:200]
        return
    accs = (data.get("data") or {}).get("viewer", {}).get("accounts", [])
    if not accs:
        usage["web_error"] = "계정 데이터 없음"
        return
    for name in ("today", "yesterday"):
        rows = accs[0].get(name) or []
        usage[f"web_{name}_pageviews"] = sum(x.get("count", 0) for x in rows)
        usage[f"web_{name}_visits"] = sum((x.get("sum") or {}).get("visits", 0) for x in rows)


def save_status(sa, problems, issue_url):
    """점검 결과와 사용량을 Firestore boothPosts/healthcheck 에 저장(관리 화면이 읽음).
    boothPosts 의 main 이외 문서는 규칙상 운영자만 읽을 수 있어 일반 방문자에게는 보이지 않음.
    서비스 계정(관리자 권한)으로 쓰므로 규칙과 상관없이 써짐. 1시간에 쓰기 1번."""
    if sa is None:
        print("상태 저장 건너뜀: FIREBASE_SA_KEY 없음")
        return
    run_url = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run_url = (f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
                   f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}")
    payload = {
        "checkedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "runUrl": run_url,
        "issueUrl": issue_url or "",
        "results": [dict(x, critical=is_critical(x)) for x in results],
        "criticalCount": len(problems),
        "usage": usage,
    }
    body = {"fields": {"data": {"stringValue": json.dumps(payload, ensure_ascii=False)},
                       "checkedAt": {"timestampValue": payload["checkedAt"]}}}
    try:
        s = sa_session(sa, "https://www.googleapis.com/auth/datastore")
        r = s.patch(f"{FIRESTORE_DOCS}/boothPosts/healthcheck", json=body, timeout=20)
        print(f"관리 화면용 상태 저장: HTTP {r.status_code}")
    except Exception as e:
        print(f"관리 화면용 상태 저장 실패: {type(e).__name__}")


# ---------------------------------------------------------------- 알림 (GitHub 이슈)
LABEL = "healthcheck"
OWNER_MENTION = os.environ.get("HC_NOTIFY", "@mononono97")


def report_markdown(problems):
    now = dt.datetime.now(KST).strftime("%Y-%m-%d %H:%M KST")
    run = ""
    if os.environ.get("GITHUB_RUN_ID"):
        run = (f" · [실행 기록]({os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/"
               f"{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']})")
    lines = [f"마지막 확인: {now}{run}", ""]
    if problems:
        lines += ["### 이상", ""]
        lines += [f"- {'❌' if p['status'] == 'fail' else '⚠️'} **[{p['group']}] {p['key']}**: {p['detail']}"
                  for p in problems]
        lines.append("")
    minor = [x for x in results if x["status"] in ("fail", "warn") and not is_critical(x)]
    if minor:
        lines += ["### 참고 (알림 없음)", ""]
        lines += [f"- ⚠️ [{x['group']}] {x['key']}: {x['detail']}" for x in minor]
        lines.append("")
    lines += ["<details><summary>전체 점검 결과</summary>", ""]
    mark = {"ok": "✅", "fail": "❌", "warn": "⚠️", "skip": "⏭️"}
    lines += [f"- {mark[x['status']]} [{x['group']}] {x['key']}: {x['detail']}" for x in results]
    lines += ["", "</details>", "",
              "점검 내용과 대처 방법: [사이트 헬스체크 문서](../blob/main/docs/wiki/기능-사이트-헬스체크.md)"]
    return "\n".join(lines)


# 알림(이슈·휴대폰)은 사이트를 실제로 못 쓰게 되는 "치명적" 이상에만 보냄 (사용자 요청, 2026-11-04).
# 그 밖의 이상(느림, sw.js·관리 화면·부스컷 이미지, 상품 정보 글 문서, 점검용 토큰·통계 API 문제)은
# 실행 기록 요약과(이슈가 열려 있으면) 이슈 본문의 "참고"에만 남기고 알림은 보내지 않음.
CRITICAL_SITE_KEYS = {"첫 화면", "Firebase SDK (gstatic)"}
NONCRITICAL_SYNC_KEYS = {"상품 정보 글 문서 읽기"}


def is_critical(x):
    if x["status"] not in ("fail", "warn"):
        return False
    g, key = x["group"], x["key"]
    if g == "접속":
        return x["status"] == "fail" and key in CRITICAL_SITE_KEYS
    if g == "할당량":
        return x["status"] == "warn"           # 무료 한도 기준 초과만. 토큰·통계 API 오류는 참고
    if g == "동기화":
        return x["status"] == "fail" and key not in NONCRITICAL_SYNC_KEYS
    return x["status"] == "fail"               # 배포·로그인


def signature(problems):
    # 할당량 % 처럼 매번 바뀌는 숫자는 빼고 "어떤 항목이 이상인지"만으로 같은 이상인지 판단
    return ",".join(sorted(f"{p['group']}/{p['key']}/{p['status']}" for p in problems))


def push_phone(title, problems, issue_url, recovered=False, message=None):
    """휴대폰 푸시 알림(ntfy 앱). 비밀값 NTFY_TOPIC 이 있을 때만 보냄. 실패해도 이슈 알림은 그대로."""
    topic = os.environ.get("NTFY_TOPIC", "").strip()
    if not topic:
        return
    if message:
        pass
    elif recovered:
        message = "모든 점검이 정상으로 돌아왔습니다."
    else:
        message = "\n".join(f"{'❌' if p['status'] == 'fail' else '⚠️'} {p['key']}: {p['detail']}"[:300]
                            for p in problems[:5])
    payload = {"topic": topic, "title": title, "message": message,
               # 5 = 긴급(소리·진동), 4 = 높음. 3(보통) 이하는 안드로이드 절전(Doze) 중이면 FCM 이 묶어 두었다가
               # 수십 분 뒤에 전달해서(2026-11-03 복구 알림이 38분 늦게 옴) 복구·시험 알림도 4 로 보냄.
               "priority": 4 if recovered else 5,
               "tags": ["white_check_mark"] if recovered else ["rotating_light"]}
    if issue_url:
        payload["click"] = issue_url
        payload["actions"] = [{"action": "view", "label": "이슈 열기", "url": issue_url}]
    try:
        r = requests.post(os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/") + "/", json=payload, timeout=20)
        print(f"휴대폰 알림 보냄: HTTP {r.status_code}")
    except requests.RequestException as e:
        print(f"휴대폰 알림 실패: {e}")


def sync_issue(problems):
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    api = f"https://api.github.com/repos/{repo}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    r = requests.get(f"{api}/issues", headers=h, params={"labels": LABEL, "state": "open", "per_page": 5}, timeout=20)
    r.raise_for_status()
    open_issues = [i for i in r.json() if "pull_request" not in i]
    issue = open_issues[0] if open_issues else None
    sig = signature(problems)
    body = f"<!-- hc-sig: {sig} -->\n" + report_markdown(problems)

    if not problems:
        if issue:
            requests.post(f"{api}/issues/{issue['number']}/comments", headers=h, timeout=20,
                          json={"body": "✅ 모든 점검이 정상으로 돌아와 이슈를 닫습니다.\n\n" + report_markdown([])})
            requests.patch(f"{api}/issues/{issue['number']}", headers=h, timeout=20,
                           json={"state": "closed", "state_reason": "completed"})
            print(f"정상으로 돌아옴 → 이슈 #{issue['number']} 닫음")
            push_phone("✅ 사이트 헬스체크 정상 복구", [], issue["html_url"], recovered=True)
        else:
            print("이상 없음 → 알림 없음")
        return None

    summary = ", ".join(f"{p['key']}" for p in problems[:3]) + (" 외" if len(problems) > 3 else "")
    title = f"🚨 사이트 헬스체크 이상: {summary}"
    if issue is None:
        requests.post(f"{api}/labels", headers=h, timeout=20,
                      json={"name": LABEL, "color": "d73a4a", "description": "사이트 헬스체크 자동 알림"})
        r = requests.post(f"{api}/issues", headers=h, timeout=20,
                          json={"title": title, "body": f"{OWNER_MENTION} 사이트 점검에서 이상을 찾았습니다.\n\n{body}",
                                "labels": [LABEL]})
        r.raise_for_status()
        print(f"새 이상 → 이슈 #{r.json()['number']} 엶")
        push_phone(title, problems, r.json().get("html_url"))
        return r.json().get("html_url")

    old = re.search(r"<!-- hc-sig: (.*?) -->", issue.get("body") or "")
    head = f"{OWNER_MENTION} 사이트 점검에서 이상을 찾았습니다.\n\n"
    requests.patch(f"{api}/issues/{issue['number']}", headers=h, timeout=20,
                   json={"title": title, "body": head + body})
    if not old or old.group(1) != sig:
        requests.post(f"{api}/issues/{issue['number']}/comments", headers=h, timeout=20,
                      json={"body": "이상 내용이 바뀌었습니다.\n\n" + report_markdown(problems)})
        print(f"이상 내용 바뀜 → 이슈 #{issue['number']} 에 댓글")
        push_phone(title, problems, issue["html_url"])
    else:
        print(f"같은 이상 계속 → 이슈 #{issue['number']} 본문만 갱신(알림 없음)")
    return issue["html_url"]


def run_checks(args):
    """모든 점검을 돌리고 치명적 이상 목록을 돌려줌."""
    if not args.skip_site:
        check_site()
    check_login_config()
    try:
        sa = load_sa()
    except ValueError:
        sa = None
        add("동기화", "서비스 계정 키", "fail", "FIREBASE_SA_KEY 가 JSON 형식이 아님 (키 파일 내용을 통째로 붙여 넣어야 함)")
    check_public_firestore()
    check_sync(sa)
    check_firestore_quota(sa)
    check_cloudflare_quota()
    run_checks.sa = sa
    return [x for x in results if is_critical(x)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue", action="store_true", help="결과로 GitHub 이슈 열기·갱신·닫기")
    ap.add_argument("--push-test", action="store_true", help="휴대폰 알림(ntfy) 시험 알림 보내기")
    ap.add_argument("--show-usage", action="store_true", help="사용량(방문자·로그인 사용자)을 모을 수 있는지 확인(숫자는 출력 안 함)")
    ap.add_argument("--save-status", action="store_true", help="이슈는 건드리지 않고 관리 화면용 상태만 저장")
    ap.add_argument("--skip-site", action="store_true", help="접속·배포 점검 건너뛰기(사이트에 못 닿는 곳에서 시험할 때)")
    args = ap.parse_args()

    if args.push_test:
        if os.environ.get("NTFY_TOPIC", "").strip():
            push_phone("🔔 사이트 헬스체크 알림 시험", [], None, recovered=True,
                       message="이 알림이 보이면 휴대폰 알림 설정이 끝난 것입니다.")
        else:
            print("휴대폰 알림 시험: 비밀값 NTFY_TOPIC 이 없어 보내지 않음")
    problems = run_checks(args)
    if problems:
        # 치명적 이상은 순간적인 끊김일 수 있어 1분 뒤 전체를 한 번 더 점검하고, 그때도 같을 때만 알림
        print(f"\n치명적 이상 {len(problems)}건 → 60초 뒤 다시 점검\n")
        time.sleep(60)
        results.clear()
        problems = run_checks(args)
    minor = [x for x in results if x["status"] in ("fail", "warn") and not is_critical(x)]
    print()
    print(f"치명적 이상 {len(problems)}건 / 참고(알림 없음) {len(minor)}건 / 점검 {len(results)}건")
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            f.write("## 사이트 헬스체크\n\n" + report_markdown(problems) + "\n")
    if args.issue:
        issue_url = sync_issue(problems)
        collect_auth_usage(run_checks.sa)
        collect_cf_usage()
        save_status(run_checks.sa, problems, issue_url)
        return 0
    if args.show_usage or args.save_status:
        collect_auth_usage(run_checks.sa)
        collect_cf_usage()
        # 저장소가 공개라 실행 기록도 공개 → 숫자는 찍지 않고 어떤 항목을 모았는지만
        print("사용량 수집:", ", ".join(sorted(usage)) or "없음")
    if args.save_status:
        save_status(run_checks.sa, problems, None)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
