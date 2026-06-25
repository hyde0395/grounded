# grounded → Codex CLI 포팅 Implementation Plan (trimmed)

> **변경 이력:** 초안은 정규화 어댑터(`harness.py`) + apply_patch 파서 중심의 7태스크였으나,
> 2026-06-13 페이로드 사실 확정(공식 문서 + openai/codex #18491/#18391) 후 범위가 대폭
> 축소됨. Codex가 Claude의 hook 계약을 미러링(`tool_name:"Bash"`, 문자열 command, exit 2,
> `hookSpecificOutput`)하므로 **코어·entrypoint 무수정, 어댑터 불필요.** G-1은 Codex
> `read_file`/`grep`가 hook을 안 띄워 근거를 못 보므로 **기본 OFF**(오탐 방지 + 내장 규율과 중복).
> 설계: `docs/superpowers/specs/2026-06-13-codex-port-design.md`.

**Goal:** grounded의 차별화 가치(G-2/G-3/sed-i)를 Codex CLI에서 작동시킨다 — 등록 템플릿 +
정직한 지원 문서 + 라이브 검증.

**Architecture:** 코드 변경 없음. `.codex/hooks.json` 등록 + README. Codex Bash 페이로드는
기존 entrypoint가 그대로 처리.

---

## 완료된 산출물 (이 세션에서 생성)

- [x] `.codex/hooks.json` — SessionStart/PostToolUse(matcher `Bash`)/PreToolUse(matcher `Bash`)
  등록 템플릿. `/ABSOLUTE/PATH/TO/grounded` 치환용 플레이스홀더.
- [x] `README.md` `### With OpenAI Codex CLI` 섹션 — 설치 + hook trust 안내 + 규칙별 정직한
  지원 표(G-2/G-3/G-1s/freshness ✅, G-1 기본 OFF + 이유).
- [x] `docs/superpowers/specs/2026-06-13-codex-port-design.md` — 정정된 설계.

---

## 남은 작업 — 라이브 검증 (사용자측: Codex 설치 필요)

이 머신엔 Codex CLI가 없어 자동 검증 불가. 아래는 Codex 설치 후 사람이 1회 실행.

### Task A: 등록 + trust

- [ ] `.codex/hooks.json`의 `/ABSOLUTE/PATH/TO/grounded`를 실제 경로로 치환해 `~/.codex/hooks.json`
  (또는 테스트 프로젝트의 `.codex/hooks.json`)에 배치.
- [ ] Codex 첫 실행 시 hook trust(해시 신뢰) 승인.
- [ ] SessionStart 발화 → `.grounded/ledger.json` 생성 확인.

### Task B: G-2 (미확인 패키지 설치 차단)

- [ ] Codex에 `pip install reqests`(또는 명백한 환각명) 실행 요청.
- [ ] Expected: PreToolUse `exit 2`로 차단 + stderr `[grounded G-2] Package 'reqests' was not
  found on PyPI…`가 모델에 전달.

### Task C: G-3 (죽은 링크)

- [ ] Codex에 `curl https://<NXDOMAIN-or-404>` 실행 요청.
- [ ] Expected: 404/NXDOMAIN이면 STOP, 403/타임아웃이면 WARN(설계대로).

### Task D: G-1s (미독 파일 sed -i 경고)

- [ ] 읽지 않은 기존 파일에 `sed -i 's/a/b/' <file>` 요청.
- [ ] Expected: WARN이 `additionalContext`로 주입(차단 아님).

### Task E: SessionStart `source` 필드 확인 + 필요 시 별칭 추가

- [ ] Task A 캡처에서 Codex SessionStart 페이로드의 resume/compact 신호 필드명 확인.
- [ ] `source`가 아니면 `hooks/session_start.py`의 `source` 분기에 Codex 필드 별칭 추가
  (resume/compact에서 ledger 보존). 미확인 시 항상 reset(보수적)이라 기능상 안전, 다만 resume
  직후 오탐 가능성 → 별칭 추가가 개선.

### Task F: 결과 기록 + 커밋

- [ ] B/C/D 실제 동작을 README 또는 spec §7에 "Codex 라이브 검증(YYYY-MM-DD)" 줄로 기록
  (Claude 검증 기록과 동일 양식).
- [ ] 커밋(CLAUDE.md 규칙: Co-Authored-By 금지, main이면 브랜치 먼저).

---

## 보류 (수요 생기면)

- **G-1 apply_patch STOP** — 기본 OFF 유지. opt-in 수요가 실제로 생기면 apply_patch 파서
  (`tool_input.command` 패치 텍스트 → 대상 파일) + WARN-only 게이트를 별도로 설계.
- Aider/Continue 등 타 하니스 — hook 표면이 달라 별도 프로젝트.
