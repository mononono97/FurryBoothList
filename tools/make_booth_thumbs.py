"""부스 목록 카드에 쓰는 작은 부스컷(썸네일)을 만든다.

- 입력: booth-cuts/*.webp (상세 창·크게 보기에 쓰는 원본)
- 출력: booth-cuts/thumbs/부스번호.webp (가로 320px, 카드에는 약 100~130px로 보이므로 고해상도 화면에서도 선명함)
- 목록은 썸네일만 받아서, "전체" 탭을 내려 봐도 원본 100장(약 8MB)을 받지 않게 함 (2026-10-08).
- 부스컷 원본을 바꾸거나 새로 넣으면 이 스크립트를 다시 실행할 것.

사용법: python3 tools/make_booth_thumbs.py [--src booth-cuts] [--width 320] [--quality 78]
필요: pip install pillow
"""
import argparse
import glob
import os

from PIL import Image


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="booth-cuts")
    ap.add_argument("--width", type=int, default=320)
    ap.add_argument("--quality", type=int, default=78)
    args = ap.parse_args()

    out_dir = os.path.join(args.src, "thumbs")
    os.makedirs(out_dir, exist_ok=True)
    total_in = total_out = 0
    for path in sorted(glob.glob(os.path.join(args.src, "*.webp"))):
        with Image.open(path) as im:
            im = im.convert("RGB")
            if im.width > args.width:
                im = im.resize((args.width, round(im.height * args.width / im.width)), Image.LANCZOS)
            out = os.path.join(out_dir, os.path.basename(path))
            im.save(out, "WEBP", quality=args.quality, method=6)
        total_in += os.path.getsize(path)
        total_out += os.path.getsize(out)
    print(f"원본 {total_in / 1e6:.1f}MB → 썸네일 {total_out / 1e6:.1f}MB ({out_dir})")


if __name__ == "__main__":
    main()
