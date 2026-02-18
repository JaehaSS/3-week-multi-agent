# Gemini MCP Agent

Gemini SDK로 MCP 서버에 연결하여 도구를 호출하는 에이전트. 단일 에이전트 모드와 멀티에이전트(오케스트레이터) 모드를 지원한다.

## 환경 설정

### 1. 사전 요구사항

- Python 3.12 이상
- [uv](https://docs.astral.sh/uv/) 설치

```bash
# uv 설치 (Windows PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. 의존성 설치

```bash
uv sync
```

이 명령어가 자동으로:

- `.venv` 가상환경 생성
- `google-genai`, `mcp` 패키지 설치

> OneDrive 폴더에서 하드링크 오류가 발생하면:
>
> ```bash
> uv sync --link-mode=copy
> ```

### 3. Gemini API 키 설정

`agent.py`의 `__init__`에서 API 키를 직접 설정하거나, 환경변수를 사용:

```bash
# Windows PowerShell
$env:GEMINI_API_KEY = "your-api-key"

# Linux/Mac
export GEMINI_API_KEY="your-api-key"
```

API 키는 [Google AI Studio](https://aistudio.google.com/apikey)에서 발급.

### 4. MCP 서버 설정

`config.json`에 사용할 MCP 서버를 정의:

```json
{
  "mcpServers": {
    "mcp-devdiary": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/JaehaSS/mcp-devdiary.git",
        "mcp-devdiary"
      ],
      "env": {
        "GITHUB_TOKEN": "Github_TOKEN",
        "GITHUB_USERNAME": "GITHUB_USER_NAME"
      }
    }
  }
}
```

여러 MCP 서버를 동시에 연결할 수 있다.

## 실행

### 단일 에이전트 모드 (기본)

```bash
uv run python main.py
```

config 파일 경로를 지정하려면:

```bash
uv run python main.py path/to/config.json
```

실행하면 대화형 프롬프트가 나타난다:

```
[연결됨] mcp-devdiary: ['get_commits', 'get_weekly_activity', 'get_activity_for_resume', ...]

=== Gemini MCP Agent ===
종료하려면 'quit' 또는 'exit'를 입력하세요.

You:
```

### 멀티에이전트 모드

`--multi` 플래그를 추가하면 오케스트레이터 기반 멀티에이전트 시스템으로 실행된다:

```bash
uv run python main.py --multi
```

config 경로와 함께 사용:

```bash
uv run python main.py --multi path/to/config.json
```

실행하면 4개 에이전트가 초기화된다:

```
[연결됨] mcp-devdiary: ['get_commits', 'get_weekly_activity', ...]

[멀티에이전트 초기화 완료]
  Orchestrator: 작업 분석 & 위임
  Analyst: 데이터 수집 (MCP 도구 사용)
  Writer: 문서 작성
  Reviewer: 품질 검토

=== Gemini Multi-Agent System ===
종료하려면 'quit' 또는 'exit'를 입력하세요.

You:
```

종료: `quit`, `exit`, 또는 `Ctrl+C`

## 사용 예시 (mcp-devdiary)

### 연결되는 MCP 도구 목록

[mcp-devdiary](https://github.com/JaehaSS/mcp-devdiary)가 제공하는 7개 도구가 자동으로 Gemini에 등록된다:

| Tool | Description |
|------|-------------|
| `get_commits` | GitHub 주간 커밋 내역 및 통계 조회 |
| `get_weekly_activity` | 커밋 + PR + 에이전트 세션 통합 주간 활동 |
| `get_activity_for_resume` | 이력서용 다주간 활동 데이터 |
| `get_agent_sessions` | Claude Code / Codex 세션 히스토리 |
| `get_weekly_timeline` | 전체 활동의 시간순 타임라인 |
| `get_enriched_weekly_report` | Few-shot 예제 포함 주간 리포트 |
| `get_weekly_insights` | 패턴 분석 및 개선 제안 |

### get_commits 예제

이번 주 커밋 내역을 가져오는 예시:

```
You: 이번 주 내 커밋 내역을 알려줘
  [도구 호출] get_commits({"username": "Jjaeha", "week_offset": 0})

Gemini: 이번 주(2026-02-09 ~ 2026-02-15) 커밋 내역입니다:

  - feat: MCP 에이전트 초기 구현 (agent.py, main.py)
  - docs: README 작성
  - fix: OneDrive 하드링크 오류 해결

  총 3개 커밋, 5개 파일 변경
```

지난 주 커밋을 보려면:

```
You: 지난 주 커밋 보여줘
  [도구 호출] get_commits({"username": "Jjaeha", "week_offset": -1})

Gemini: 지난 주(2026-02-02 ~ 2026-02-08) 커밋 내역입니다: ...
```

### 기타 질문 예시

```
You: 이번 주 작업 요약해줘
  [도구 호출] get_enriched_weekly_report({"username": "Jjaeha", "week_offset": 0})
```

```
You: 최근 한 달 활동으로 이력서 항목 만들어줘
  [도구 호출] get_activity_for_resume({"username": "Jjaeha", "weeks_range": 4})
```

```
You: 내 코딩 패턴 분석해줘
  [도구 호출] get_weekly_insights({"username": "Jjaeha", "week_offset": 0})
```

## 멀티에이전트 시스템

### 아키텍처

오케스트레이터 패턴을 사용한다. Orchestrator가 모든 통신을 중개하며, 전문 에이전트들은 서로 직접 대화하지 않는다.

```
사용자 입력
    │
┌───┴───────────────────┐
│   Orchestrator        │  ← 작업 분석 & 위임 결정
└───┬───────┬───────┬───┘
    │       │       │
    ▼       ▼       ▼
┌───────┐ ┌───────┐ ┌────────┐
│Analyst│ │Writer │ │Reviewer│
└───┬───┘ └───────┘ └────────┘
    │
[공유 MCP 연결]
```

### 에이전트 역할

| 에이전트 | 역할 | MCP 도구 |
|---------|------|----------|
| **Orchestrator** | 사용자 요청을 분석하여 실행 계획(JSON) 수립. 최종 결과 통합 | 없음 |
| **Analyst** | MCP 도구로 데이터 수집 및 정리 | 전체 사용 가능 |
| **Writer** | 수집된 데이터를 보고서/요약/이력서 항목으로 작성 | 없음 |
| **Reviewer** | 작성된 결과물의 품질 평가 및 개선 제안 | 없음 |

### 동작 원리

- 모든 에이전트는 같은 Gemini 모델(`gemini-2.5-flash`)을 사용하며, **시스템 프롬프트**로만 역할이 구분된다
- MCP 연결은 1개만 유지하고 Analyst가 공유하여 사용한다
- 단순 질문("안녕")은 Orchestrator가 직접 응답하고, 복합 작업만 에이전트 파이프라인을 실행한다

### 멀티에이전트 사용 예시

복합 작업 요청:

```
You: 이번 주 커밋 내역으로 보고서 작성해줘

┌─ Orchestrator ──────────────────────────
│ 작업 분석 중...
└─────────────────────────────────────────

┌─ Orchestrator ──────────────────────────
│ 실행 계획: Analyst → Writer → Reviewer
└─────────────────────────────────────────

┌─ Analyst ───────────────────────────────
│ 작업: 이번 주 커밋 데이터를 수집하세요...
│ [도구 호출] get_commits({"week_offset": 0})
│ 완료 (320자)
└─────────────────────────────────────────

┌─ Writer ────────────────────────────────
│ 작업: 수집된 데이터로 보고서를 작성하세요...
│ 완료 (650자)
└─────────────────────────────────────────

┌─ Reviewer ──────────────────────────────
│ 작업: 작성된 보고서를 검토하세요...
│ 완료 (480자)
└─────────────────────────────────────────

┌─ Orchestrator ──────────────────────────
│ 최종 결과 통합 완료
└─────────────────────────────────────────

========================================
최종 결과:
========================================
(리뷰어 피드백이 반영된 최종 보고서)
```

단순 질문:

```
You: 안녕

┌─ Orchestrator ──────────────────────────
│ 작업 분석 중...
└─────────────────────────────────────────

┌─ Orchestrator ──────────────────────────
│ 직접 응답 (에이전트 불필요)
└─────────────────────────────────────────

========================================
최종 결과:
========================================
안녕하세요! 무엇을 도와드릴까요?
```

## 프로젝트 구조

```
├── agent.py         # 단일 에이전트 (Gemini + MCP 브릿지)
├── multi_agent.py   # 멀티에이전트 시스템 (Orchestrator + Specialist Agents)
├── main.py          # CLI 진입점 (--multi 플래그로 모드 선택)
├── config.json      # MCP 서버 설정
├── pyproject.toml   # uv 프로젝트 설정
└── requirements.txt # pip용 의존성 목록
```

## 동작 흐름

### 단일 에이전트 모드

1. `config.json`의 MCP 서버를 `uvx`로 실행하여 stdio 연결
2. MCP 서버의 도구 목록을 Gemini `FunctionDeclaration`으로 변환
3. 사용자 쿼리를 Gemini에 전송
4. Gemini가 도구 호출을 요청하면 MCP를 통해 실행
5. 결과를 다시 Gemini에 전달하여 최종 응답 생성
6. 도구 호출이 더 이상 없을 때까지 반복 (최대 10회)

### 멀티에이전트 모드

1. MCP 서버 연결 (단일 모드와 동일)
2. 4개 에이전트 초기화 (Orchestrator, Analyst, Writer, Reviewer)
3. 사용자 쿼리를 Orchestrator에 전달
4. Orchestrator가 Gemini에게 작업 분석 요청 → JSON 실행 계획 수립
5. 계획에 따라 전문 에이전트를 순차 호출 (이전 결과를 다음 에이전트에 전달)
6. Orchestrator가 모든 결과를 통합하여 최종 응답 생성
