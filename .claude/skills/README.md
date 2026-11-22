# 프로젝트 스킬 (디자인)

이 폴더의 스킬은 이 저장소에서 Claude Code를 열면(프로젝트 스레드 포함) 자동으로 쓸 수 있습니다.
사이트 배포에는 들어가지 않습니다(`.assetsignore`에 `.claude` 포함).

| 스킬 이름 | 폴더 | 출처 | 가져온 커밋 | 라이선스 |
|---|---|---|---|---|
| `hallmark` | `hallmark/` | https://github.com/nutlope/hallmark (`skills/hallmark`) | `04977e15f3dfd6ee4b74c887dac3eb52665d6c92` | MIT (`hallmark/LICENSE`) |
| `design-taste-frontend` | `design-taste-frontend/` | https://github.com/leonxlnx/taste-skill (`skills/taste-skill`) | `f6b76575f949707b4e3c37cd86cb44b0b98643e8` | MIT (`design-taste-frontend/LICENSE`, 저장소 루트에서 복사) |
| `impeccable` | `impeccable/` | https://github.com/pbakaus/impeccable (`.claude/skills/impeccable`, 빌드된 Claude Code용 스킬) | `b49177d32788f2dc585c42831d845dacf1ebed2c` | Apache-2.0 (`impeccable/LICENSE`, `impeccable/NOTICE.md`) |

가져온 날짜: 2026-11-22. 파일 내용은 원본 그대로이며 고치지 않았습니다.

## 넣지 않은 파일
- hallmark: `scripts/install.mjs`(홈 폴더 `~/.claude/skills` 등에 전역 설치), `scripts/install-hook.mjs`
  (`.claude/settings.json`에 편집 훅을 써 넣는 설치기), `agents/openai.yaml`(Codex용 메타데이터).
  `references/flow.md`가 install-hook.mjs를 선택 사항으로 언급하지만 없어도 스킬은 동작합니다.
- taste-skill: 저장소 루트의 `install.sh`, 플러그인/마켓플레이스 메타데이터(`.claude-plugin`, `.codex-plugin`), 예제·테스트.
- impeccable: 다른 에이전트용 사본들, `.claude/settings.json`(훅 설정), 플러그인·확장 프로그램·CLI 소스.

## 알아둘 동작
- `design-taste-frontend`의 SKILL.md에는 **Stop 훅**이 들어 있어, 이 스킬을 쓰는 중에는 턴이 끝날 때
  `scripts/gate.mjs`(Node)가 자동 실행됩니다. 만든 페이지를 검사만 하고, 빌드 기록(`.taste-skill/`)이 없으면 바로 끝납니다.
- `impeccable/scripts/impeccable`은 처음 실행할 때 GitHub 릴리스에서 엔진 바이너리
  (`engine-v0.1.27`)를 내려받고 `engine-checksums.txt`의 sha256과 맞을 때만 실행합니다.
  바이너리 자체는 소스로 검토할 수 없다는 점은 알아두세요.
- `hallmark/scripts/imagegen.mjs`는 `TOGETHER_API_KEY`가 설정된 경우에만 together.ai로 이미지 생성 요청을 보냅니다(기본은 꺼짐).

## 업데이트하는 법
원본 저장소의 새 커밋에서 같은 폴더를 다시 복사하고, 위 표의 커밋 해시를 바꾼 뒤 PR로 올립니다.
가져온 스크립트는 외부 코드이므로 바뀐 부분을 훑어보고 넣습니다.
