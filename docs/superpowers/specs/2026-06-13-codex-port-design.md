# grounded → Codex CLI 포팅 설계

- 날짜: 2026-06-13
- 상태: 설계 (구현 전)
- 범위: grounded를 OpenAI **Codex CLI**에서도 동작하게 한다. "로컬 OpenAI 모델"은
  Codex CLI에 로컬 OpenAI-호환 엔드포인트를 물려 쓰는 경우로 한정한다 — hook은
  모델이 아니라 하니스의 기능이므로 타깃은 하니스(Codex)다.

## 1. 동기 · 검증된 전제

grounded의 코어는 세 hook이 로컬 ledger 하나를 공유하는 구조다(`CLAUDE.md` 참조).
이 구조가 다른 하니스로 옮겨갈 수 있는지는 **그 하니스가 PreToolUse/PostToolUse/
SessionStart에 준하는 hook 경계와 `exit 2` 차단을 제공하는가**에 전적으로 달려 있다.

2026-06-13 공식 문서(`developers.openai.com/codex/hooks`)와 DeepWiki로 대조한 결과,
Codex CLI는 Claude Code와 **거의 1:1 상위호환**인 hook 표면을 갖는다:

| grounded 필요 | Claude Code | Codex CLI | 비고 |
|---|---|---|---|
| 상태 초기화 | SessionStart | SessionStart (begin/resume/clear/compact) | ✅ |
| 근거 적립 | PostToolUse (`tool_response`) | PostToolUse (`tool_response` stdin 포함) | ✅ |
| 행동 검문 | PreToolUse + `exit 2` | PreToolUse + `exit 2` | ✅ |
| 차단 의미 | exit 2 = 차단 + stderr→모델 | exit 2 = 차단 + stderr→모델 | ✅ 문구까지 동일 |
| WARN 주입 | `hookSpecificOutput.additionalContext` | 동일 키 + `permissionDecision`/`decision:"block"` | ✅ |
| 등록 | `settings.json` | `~/.codex/hooks.json` 또는 `config.toml` (+ `.codex/` 프로젝트) | ⚠️ 포맷만 다름 |
| stdin | JSON (`cwd`,`tool_name`,`tool_input`,`tool_response`) | JSON (`session_id`,`cwd`,`hook_event_name`,`turn_id`,`tool_name`,`tool_input`,`tool_response`) | ⚠️ 일부 필드 추가/상이 |

결론: **순수 코어(`verdict.py`/`ledger_io.py`/`shell_scan.py`/`registry.py`/
`urlcheck.py`)는 무수정 재사용.** Claude에 묶인 부분은 thin entrypoint 3개의
stdin 파싱·출력 포맷·tool_name 어휘뿐이다.

## 2. 검증된 페이로드 사실 (공식 문서 + openai/codex 이슈 #18491, #18391 대조)

2026-06-13 확정. 이전 초안의 "미해결 가정" 다수가 여기서 해소됐다.

1. **셸 `tool_name`은 `"Bash"`** — Codex가 Claude 어휘로 canonical화함. `tool_input.command`는
   **문자열**(argv 배열 아님). → **Bash 경로엔 정규화가 전혀 필요 없다.** 기존 entrypoint가
   Codex Bash 페이로드를 그대로 처리한다.
2. **exit 2 = 차단 + stderr 모델 피드백 — 확인됨**(공식 hooks 문서). `hookSpecificOutput.
   additionalContext`/`permissionDecision`도 동일 키.
3. **apply_patch는 v0.123.0(PR #18391, 2026-04-23)부터 PreToolUse 발화.** `tool_name:
   "apply_patch"`, 패치 텍스트는 `tool_input.command`(문자열). matcher 별칭 `Edit`/`Write`.
4. **결정적 제약 — `read_file`·`grep`는 hook을 발화하지 않는다**(이슈 #18491:
   "read_file and grep have no ToolHandler with pre/post hook payloads"). → Codex 네이티브
   읽기는 ledger에 **근거를 남기지 않는다.** 단, Codex의 apply_patch 지침이 모델에게
   **"shell-first: `cat`으로 읽어라"**를 프롬프트하므로, `cat` 경로 읽기는 Bash로 발화 →
   기존 `cat_targets`가 그대로 적립한다(정규화 불필요).
5. **`updatedInput`(입력 재작성)은 거부됨** — grounded는 입력을 재작성하지 않고 exit 2 /
   additionalContext만 쓰므로 무관.

### G-1(편집 게이트)을 Codex에서 켜지 않는 이유 — 핵심 설계 판단

- Claude에서도 G-1 편집 차단은 **내장 read-before-edit와 중복**이다(`CLAUDE.md` E2E 검증
  기록). 차별화 본체는 처음부터 G-2/G-3 + Bash 우회(sed -i)였다.
- Codex도 프롬프트 차원 shell-first 규율 + 자체 apply_patch 동작으로 사실상 같은 커버리지를
  갖는다. 게다가 우리 hook은 `read_file` 읽기를 못 보므로, G-1을 STOP으로 켜면 `read_file`로
  읽은 파일의 편집을 막는 **오탐 기계**가 된다 — spec §05(오탐<누락) 정면 위반.
- **결정: Codex에서 G-1은 기본 OFF.** grounded엔 이미 규칙별 토글(`.grounded/config.json`
  `{"g-1": false}` / `GROUNDED_DISABLE`)이 있으므로 **새 코드 0줄**, 등록·문서만으로 끝.
  방어 계층을 원하는 사용자는 opt-in으로 켤 수 있게 문서화(권장 안 함).

## 3. 아키텍처 — 어댑터 없음, 등록 + 문서가 전부

Codex가 Claude의 hook 계약(`tool_name:"Bash"`, 문자열 command, exit 2,
`hookSpecificOutput`)을 의도적으로 미러링했으므로, **정규화 어댑터(`harness.py`/`adapters/`)는
불필요**하다. 순수 코어·entrypoint 전부 무수정.

신규 파일은 사실상 등록 템플릿과 문서뿐:

- `.codex/hooks.json` (신규) — SessionStart/PostToolUse/PreToolUse를 matcher `Bash`로 등록.
  (apply_patch는 G-1 기본 OFF이므로 등록하지 않거나, opt-in 문서에만.)
- `README.md` Codex 섹션 (신규) — 설치 + Codex hook trust(해시 신뢰) 절차 + 정직한
  지원 범위 표기(아래 §6).
- (선택) `.grounded/config.json` 권장 프리셋에 `{"g-1": false}` 안내.

검증해야 할 것은 코드가 아니라 **라이브 동작 1회**다(§7): Codex 설치 후 G-2/G-3/sed-i가
실제로 발화·차단하는지.

## 4. SessionStart resume/compact 신호

Claude는 payload의 `source`(startup/resume/clear/compact)로 ledger 보존 여부를 가른다.
Codex SessionStart도 begin/resume/clear/compact를 구분하나 필드명이 다를 수 있다 — 라이브
캡처로 확인한다. 미확인 시 항상 reset(=보수적, 단 resume 직후 오탐 가능성은 Claude와 동일).
이는 `session_start.py`의 `source` 분기에 Codex 필드 별칭을 추가하는 소폭 수정으로 흡수한다.

## 5. 판정 모델 · 오탐<누락 — 불변

Codex에서도 PASS/WARN/STOP 모델과 "오탐<누락" 철학은 그대로다. fail-open 지점은 기존과
동일(ledger corrupt/부재 → 통과). Codex 고유 추가 위험은 §2-4의 read_file 근거 부재뿐이며,
이를 G-1 기본 OFF(§2)로 회피한다.

## 6. 출시 범위 · 정직한 지원 표기

| 규칙 | Codex 지원 | 근거 |
|---|---|---|
| **G-2 패키지 존재** | ✅ 완전 | Bash(pip/npm/...) 발화, 선행 읽기 불필요 |
| **G-3 링크 실존** | ✅ 완전 | Bash `curl`/`wget` 발화 (Codex엔 WebFetch 전용 도구 없음 → 셸 경로만, 손실 없음) |
| **G-1s sed -i 우회** | ✅ 작동 (WARN) | Bash in-place write 발화 |
| **freshness** | ✅ 작동 | Bash write 대상에 적용 |
| **G-1 apply_patch 편집** | ⚠️ 기본 OFF | read_file 근거 부재 → STOP 시 오탐. 내장 규율과 중복. opt-in만 |

→ grounded의 **차별화 본체(G-2/G-3/sed-i)가 Codex에서 그대로 작동.** README에 이 표를
그대로 실어 과대약속을 피한다.

## 7. 테스트 · 검증 전략

- 기존 210개 오프라인 테스트는 코어 대상이라 **무수정 그대로 통과**(코드 변경이 거의 없으므로
  회귀 위험 자체가 낮음).
- 코드 변경이 §4(SessionStart Codex 필드 별칭) 정도로 국한되면 그에 대한 단위 테스트만 추가.
- **라이브 검증(핵심)**: Codex 설치 후 headless로 (a) 미확인 패키지 설치 차단(G-2),
  (b) 404/NXDOMAIN 링크 차단(G-3), (c) sed -i 미독 파일 경고(G-1s) 각 1회. Claude
  라이브 검증 기록과 동일 양식으로 남긴다.

## 8. 만들지 않는 것 (YAGNI)

- **정규화 어댑터(`harness.py`/`adapters/`)** — §3에서 불필요로 판명. 만들지 않는다.
- **Codex G-1 STOP** — §2의 오탐 이유로 기본 OFF. apply_patch 파서/다중 파일 게이트도
  지금 만들지 않는다(opt-in 수요가 실제로 생기면 그때).
- Aider/Continue/범용 프록시 어댑터 — hook 표면이 전혀 달라 이번 범위 아님.
- Codex 전용 신규 규칙 — 규칙 셋은 하니스 독립. 추가 안 함.
