#!/usr/bin/env python3
"""버그 제보 기록의 `code xxxxxxxx` 값이 어느 커밋의 index.html 인지 찾는 도구.

사이트(index.html)의 본문 <script>(const STORAGE_PREFIX = 가 있는 것) 내용을
브라우저와 같은 방식(FNV-1a 32bit, UTF-16 코드 단위)으로 해시함. index.html 의 codeFingerprint() 와 같은 계산.

사용법 (저장소 폴더에서):
  python3 tools/code_fingerprint.py              # 지금 index.html 의 지문
  python3 tools/code_fingerprint.py 1a2b3c4d     # main 최근 300개 커밋 중 이 지문인 커밋 찾기
"""
import subprocess
import sys

MARK = "const STORAGE_PREFIX ="


def fingerprint(html: str) -> str:
    html = html.replace("\r\n", "\n").replace("\r", "\n")
    i = html.find(MARK)
    if i < 0:
        return "unknown"
    start = html.rfind("<script>", 0, i) + len("<script>")
    end = html.find("</script>", i)
    text = html[start:end]
    h = 0x811C9DC5
    data = text.encode("utf-16-le")
    for k in range(0, len(data), 2):
        h ^= data[k] | (data[k + 1] << 8)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


def main():
    if len(sys.argv) < 2:
        with open("index.html", encoding="utf-8") as f:
            print(fingerprint(f.read()))
        return
    want = sys.argv[1].lower()
    ref = sys.argv[2] if len(sys.argv) > 2 else "origin/main"
    commits = subprocess.run(["git", "log", "--format=%h %ad %s", "--date=short", "-300", ref, "--", "index.html"],
                             capture_output=True, text=True, check=True).stdout.splitlines()
    for line in commits:
        sha = line.split()[0]
        html = subprocess.run(["git", "show", f"{sha}:index.html"], capture_output=True, check=True).stdout.decode("utf-8")
        if fingerprint(html) == want:
            print("찾음:", line)
            return
    print("최근 커밋 중 같은 지문이 없습니다(아직 병합 전 코드이거나 300개보다 오래됨).")


if __name__ == "__main__":
    main()
