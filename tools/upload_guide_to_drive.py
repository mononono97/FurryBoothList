"""사용법 설명서 PDF(docs/guide/)를 구글 드라이브의 기존 파일에 "새 버전"으로 올림.

- 새 파일을 만들지 않고 같은 파일 ID의 내용만 바꾸므로 드라이브 공유 링크(사이트 도움말 버튼)가 그대로임.
- 드라이브 "버전 관리"에 이전 판이 남고, keepRevisionForever=true 로 올려 30일·100개 자동 삭제 대상에서도 빠짐.
- 올릴 파일 ID는 index.html 의 HELP_GUIDE_URL(ko/en/jp)에서 읽음. 사이트 링크와 업로드 대상이 어긋날 일이 없음.
- 드라이브 쪽 내용(md5)이 이미 같으면 건너뜀(같은 내용으로 버전이 쌓이지 않게).

인증: 서비스 계정 JSON 키를 환경 변수 GDRIVE_SA_KEY 로 받음(GitHub Actions 비밀값).
      드라이브의 세 파일은 서비스 계정 이메일에 "편집자"로 공유되어 있어야 함.
      파일 소유자는 사용자 계정이라 용량도 사용자 드라이브 기준(서비스 계정은 용량이 없어 새 파일은 못 만듦).

사용:
  python tools/upload_guide_to_drive.py            # 바뀐 판만 업로드
  python tools/upload_guide_to_drive.py --dry-run  # 무엇을 올릴지만 출력 (키 없이도 실행 가능)
  python tools/upload_guide_to_drive.py --only ko,en
"""
import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_DIR = os.path.join(ROOT, "docs", "guide")
INDEX_HTML = os.path.join(ROOT, "index.html")

# 언어 → 저장소 안 PDF 파일 이름 (프로젝트 공유 폴더 guide/ 의 이름과 같게 둠)
LOCAL_FILES = {
    "ko": "Furst-Class-부스목록-사용법.pdf",
    "en": "Furst-Class-Booth-List-Guide-EN.pdf",
    "jp": "Furst-Class-Booth-List-Guide-JP.pdf",
}

API = "https://www.googleapis.com/drive/v3/files/"
UPLOAD_API = "https://www.googleapis.com/upload/drive/v3/files/"
FIELDS = "id,name,mimeType,md5Checksum,size,modifiedTime,headRevisionId,capabilities/canEdit"


def drive_ids_from_index():
    """index.html 의 HELP_GUIDE_URL 에서 언어별 드라이브 파일 ID를 꺼냄."""
    with open(INDEX_HTML, encoding="utf-8") as f:
        html = f.read()
    m = re.search(r"const HELP_GUIDE_URL\s*=\s*\{(.*?)\};", html, re.S)
    if not m:
        sys.exit("index.html 에서 HELP_GUIDE_URL 을 찾지 못함")
    ids = dict(re.findall(r'(\w+)\s*:\s*"https://drive\.google\.com/file/d/([\w-]+)', m.group(1)))
    missing = [k for k in LOCAL_FILES if k not in ids]
    if missing:
        sys.exit(f"HELP_GUIDE_URL 에 {missing} 판 드라이브 링크가 없음")
    return ids


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def session():
    raw = os.environ.get("GDRIVE_SA_KEY", "").strip()
    if not raw:
        sys.exit("GDRIVE_SA_KEY 비밀값이 비어 있음 (GitHub → Settings → Secrets and variables → Actions)")
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"])
    print(f"서비스 계정: {info.get('client_email')}")
    return AuthorizedSession(creds)


def get_meta(s, file_id):
    r = s.get(API + file_id, params={"fields": FIELDS, "supportsAllDrives": "true"})
    if r.status_code == 404:
        sys.exit(f"드라이브 파일 {file_id} 을 볼 수 없음: 서비스 계정 이메일에 이 파일을 '편집자'로 공유했는지 확인")
    r.raise_for_status()
    return r.json()


def upload_new_version(s, file_id, path):
    """재개 가능 업로드(5MB 넘는 PDF도 됨)로 기존 파일 내용만 교체 = 새 버전."""
    params = {"uploadType": "resumable", "keepRevisionForever": "true",
              "supportsAllDrives": "true", "fields": FIELDS}
    size = os.path.getsize(path)
    r = s.patch(UPLOAD_API + file_id, params=params, json={},
                headers={"X-Upload-Content-Type": "application/pdf",
                         "X-Upload-Content-Length": str(size)})
    r.raise_for_status()
    with open(path, "rb") as f:
        r = s.put(r.headers["Location"], data=f,
                  headers={"Content-Type": "application/pdf", "Content-Length": str(size)})
    r.raise_for_status()
    return r.json()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", default="", help="ko,en,jp 중 일부만 (쉼표)")
    a = ap.parse_args()

    ids = drive_ids_from_index()
    langs = [x.strip() for x in a.only.split(",") if x.strip()] or list(LOCAL_FILES)
    plan = []
    for lang in langs:
        if lang not in LOCAL_FILES:
            sys.exit(f"알 수 없는 언어: {lang}")
        path = os.path.join(GUIDE_DIR, LOCAL_FILES[lang])
        if not os.path.exists(path):
            print(f"[{lang}] {os.path.relpath(path, ROOT)} 없음 → 건너뜀")
            continue
        plan.append((lang, path, ids[lang], md5(path)))

    s = None if a.dry_run else session()
    uploaded = 0
    for lang, path, fid, local_md5 in plan:
        rel = os.path.relpath(path, ROOT)
        size_mb = os.path.getsize(path) / 1e6
        if a.dry_run:
            print(f"[{lang}] {rel} ({size_mb:.1f}MB, md5 {local_md5}) → 드라이브 {fid}")
            continue
        meta = get_meta(s, fid)
        if not meta.get("capabilities", {}).get("canEdit"):
            sys.exit(f"[{lang}] 드라이브 '{meta.get('name')}' 편집 권한 없음: 서비스 계정을 '편집자'로 공유해야 함")
        if meta.get("md5Checksum") == local_md5:
            print(f"[{lang}] 드라이브 '{meta['name']}' 내용이 이미 같음 → 건너뜀")
            continue
        new = upload_new_version(s, fid, path)
        if new.get("md5Checksum") != local_md5:
            sys.exit(f"[{lang}] 올린 뒤 md5가 다름 (드라이브 {new.get('md5Checksum')}, 저장소 {local_md5})")
        uploaded += 1
        print(f"[{lang}] '{new['name']}' 새 버전 업로드 완료 ({size_mb:.1f}MB, 버전 {new.get('headRevisionId')}, "
              f"{new.get('modifiedTime')}) https://drive.google.com/file/d/{fid}/view")
    if not a.dry_run:
        print(f"업로드 {uploaded}개, 건너뜀 {len(plan) - uploaded}개")


if __name__ == "__main__":
    main()
