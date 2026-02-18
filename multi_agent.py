import asyncio
import json
import os
from contextlib import AsyncExitStack

from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


# ──────────────────────────────────────────────
#  CLI 로그 헬퍼
# ──────────────────────────────────────────────


def log_box(agent_name: str, lines: list[str]):
    """에이전트 활동을 박스 형태로 출력한다."""
    width = 40
    header = f"┌─ {agent_name} " + "─" * max(1, width - len(agent_name) - 4)
    print(header)
    for line in lines:
        print(f"│ {line}")
    print("└" + "─" * width)
    print()


# ──────────────────────────────────────────────
#  SpecialistAgent
# ──────────────────────────────────────────────


class SpecialistAgent:
    """전문 에이전트. 시스템 프롬프트로 역할이 결정된다."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        gemini_client: genai.Client,
        gemini_tools: list[types.Tool] | None = None,
        sessions: dict[str, ClientSession] | None = None,
        tools_map: dict[str, str] | None = None,
    ):
        self.name = name
        self.system_prompt = system_prompt
        self.client = gemini_client
        self.gemini_tools = gemini_tools or []
        self.sessions = sessions or {}
        self.tools_map = tools_map or {}

    async def run(self, task: str) -> str:
        """주어진 작업을 실행하고 결과를 반환한다."""
        messages = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=task)],
            )
        ]

        log_lines = [f"작업: {task[:80]}{'...' if len(task) > 80 else ''}"]

        for _ in range(10):
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=messages,
                config=types.GenerateContentConfig(
                    system_instruction=self.system_prompt,
                    tools=self.gemini_tools or None,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(
                        disable=True
                    ),
                ),
            )

            candidate = response.candidates[0]
            parts = candidate.content.parts

            # 함수 호출이 있는지 확인
            function_calls = [p for p in parts if p.function_call]
            if not function_calls:
                texts = [p.text for p in parts if hasattr(p, "text") and p.text]
                result = "\n".join(texts) if texts else "(빈 응답)"
                log_lines.append(f"완료 ({len(result)}자)")
                log_box(self.name, log_lines)
                return result

            # 모델 응답을 대화에 추가
            messages.append(candidate.content)

            # 각 함수 호출 실행
            tool_results = []
            for part in function_calls:
                fc = part.function_call
                log_lines.append(f"[도구 호출] {fc.name}({dict(fc.args)})")

                server_name = self.tools_map.get(fc.name)
                if not server_name:
                    result_text = f"오류: 알 수 없는 도구 '{fc.name}'"
                else:
                    session = self.sessions[server_name]
                    result = await session.call_tool(fc.name, dict(fc.args))
                    result_text = "\n".join(
                        getattr(block, "text", str(block))
                        for block in result.content
                    )

                tool_results.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result_text},
                    )
                )

            messages.append(types.Content(role="user", parts=tool_results))

        log_lines.append("[경고] 최대 반복 횟수 도달")
        log_box(self.name, log_lines)
        return "[경고] 최대 반복 횟수(10)에 도달했습니다."


# ──────────────────────────────────────────────
#  Orchestrator
# ──────────────────────────────────────────────

ORCHESTRATOR_SYSTEM_PROMPT = """\
당신은 멀티에이전트 시스템의 오케스트레이터입니다.
사용자의 요청을 분석하여 전문 에이전트에게 위임할 실행 계획을 수립합니다.

사용 가능한 에이전트:
- analyst: 데이터 수집 전문가. MCP 도구를 사용하여 커밋, 활동, 인사이트 등의 데이터를 수집합니다.
- writer: 문서 작성 전문가. 수집된 데이터를 바탕으로 보고서, 요약, 이력서 항목 등을 작성합니다.
- reviewer: 검토 전문가. 작성된 결과물의 품질을 평가하고 개선점을 제안합니다.

반드시 다음 JSON 형식으로만 응답하세요:

단순 인사/질문 (에이전트 불필요):
{"needs_specialists": false, "direct_response": "응답 내용"}

복합 작업 (에이전트 필요):
{"needs_specialists": true, "plan": [{"agent": "analyst", "task": "구체적 작업 설명"}, {"agent": "writer", "task": "구체적 작업 설명"}]}

규칙:
- 데이터 수집이 필요하면 반드시 analyst를 먼저 배치
- 문서/보고서 작성이 필요하면 writer 배치
- 품질 검증이 필요하면 reviewer를 마지막에 배치
- 단순 인사나 간단한 질문은 직접 응답 (needs_specialists: false)
- 모든 에이전트를 매번 사용할 필요 없음 (필요한 것만 선택)
"""


class Orchestrator:
    """오케스트레이터 - 작업 분석 & 에이전트 위임."""

    def __init__(
        self,
        gemini_client: genai.Client,
        specialists: dict[str, SpecialistAgent],
    ):
        self.client = gemini_client
        self.specialists = specialists

    async def _create_plan(self, user_input: str) -> dict:
        """사용자 입력을 분석하여 실행 계획을 수립한다."""
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_input)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=ORCHESTRATOR_SYSTEM_PROMPT,
                response_mime_type="application/json",
            ),
        )

        text = response.candidates[0].content.parts[0].text
        return json.loads(text)

    async def process(self, user_input: str) -> str:
        """사용자 입력을 처리한다: 분석 → 위임 → 통합."""
        # 1. 실행 계획 수립
        log_box("Orchestrator", ["작업 분석 중..."])

        plan = await self._create_plan(user_input)

        # 2. 직접 응답 (단순 질문)
        if not plan.get("needs_specialists", False):
            direct = plan.get("direct_response", "(빈 응답)")
            log_box("Orchestrator", ["직접 응답 (에이전트 불필요)"])
            return direct

        # 3. 실행 계획 표시
        steps = plan.get("plan", [])
        step_summary = " → ".join(s["agent"].capitalize() for s in steps)
        log_box("Orchestrator", [f"실행 계획: {step_summary}"])

        # 4. 에이전트 순차 호출
        context = f"원래 사용자 요청: {user_input}\n\n"
        results: dict[str, str] = {}

        for step in steps:
            agent_name = step["agent"]
            task = step["task"]

            specialist = self.specialists.get(agent_name)
            if not specialist:
                log_box("Orchestrator", [f"[오류] 알 수 없는 에이전트: {agent_name}"])
                continue

            # 이전 단계 결과를 컨텍스트에 추가
            full_task = context + f"현재 작업: {task}"
            if results:
                full_task += "\n\n이전 단계 결과:\n"
                for name, result in results.items():
                    full_task += f"--- {name} 결과 ---\n{result}\n\n"

            result = await specialist.run(full_task)
            results[agent_name] = result

        # 5. 최종 결과 통합
        if len(results) == 1:
            return list(results.values())[0]

        # 여러 에이전트 결과를 Gemini로 통합
        synthesis_prompt = (
            f"원래 사용자 요청: {user_input}\n\n"
            "각 전문가의 결과를 통합하여 최종 응답을 만들어주세요:\n\n"
        )
        for name, result in results.items():
            synthesis_prompt += f"--- {name} 결과 ---\n{result}\n\n"

        response = self.client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=synthesis_prompt)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=(
                    "여러 전문가의 결과를 통합하는 편집자입니다. "
                    "리뷰어의 피드백이 있다면 이를 반영하여 최종 응답을 만드세요."
                ),
            ),
        )

        log_box("Orchestrator", ["최종 결과 통합 완료"])
        return response.candidates[0].content.parts[0].text


# ──────────────────────────────────────────────
#  Agent System Prompts
# ──────────────────────────────────────────────

ANALYST_SYSTEM_PROMPT = """\
당신은 데이터 분석 전문가입니다.
MCP 도구를 사용하여 사용자가 요청한 데이터를 수집하고 정리합니다.

규칙:
- 수집한 데이터를 구조화된 형태로 정리하여 반환
- 분석 결과만 반환하고, 보고서 작성은 하지 마세요
- 가능한 한 구체적인 수치와 사실을 포함하세요
"""

WRITER_SYSTEM_PROMPT = """\
당신은 문서 작성 전문가입니다.
제공된 데이터를 바탕으로 명확하고 구조화된 문서를 작성합니다.

규칙:
- 보고서, 요약, 이력서 항목 등 요청된 형식에 맞게 작성
- 데이터를 직접 수집하지 말고, 제공된 데이터만 사용하세요
- 읽기 쉽고 전문적인 톤으로 작성하세요
"""

REVIEWER_SYSTEM_PROMPT = """\
당신은 문서 검토 전문가입니다.
작성된 문서의 품질을 평가하고 개선점을 제안합니다.

반드시 다음 형식으로 검토하세요:
1. 전체 평가 (통과/수정필요)
2. 강점 (1-3개)
3. 개선사항 (1-3개)
4. 수정된 최종본 (개선사항을 반영한 버전)
"""


# ──────────────────────────────────────────────
#  MultiAgentSystem
# ──────────────────────────────────────────────


class MultiAgentSystem:
    """멀티에이전트 통합 시스템. MCP 연결 관리 + CLI 루프."""

    def __init__(self, config_path: str = "config.json"):
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 환경변수를 설정해주세요.")

        self.client = genai.Client(api_key=api_key)
        self.config_path = config_path
        self.sessions: dict[str, ClientSession] = {}
        self.tools_map: dict[str, str] = {}
        self.gemini_tools: list[types.Tool] = []
        self._exit_stack = AsyncExitStack()
        self.orchestrator: Orchestrator | None = None

    async def connect(self):
        """MCP 서버 연결 + 에이전트 초기화."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        all_declarations = []

        for server_name, server_config in servers.items():
            command = server_config["command"]
            args = server_config.get("args", [])
            config_env = server_config.get("env", {})

            merged_env = {**os.environ, **config_env, "UV_LINK_MODE": "copy"}

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=merged_env,
            )

            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            self.sessions[server_name] = session

            response = await session.list_tools()
            for tool in response.tools:
                self.tools_map[tool.name] = server_name

                decl = types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description or "",
                    parameters_json_schema=tool.inputSchema,
                )
                all_declarations.append(decl)

            print(f"[연결됨] {server_name}: {[t.name for t in response.tools]}")

        if all_declarations:
            self.gemini_tools = [types.Tool(function_declarations=all_declarations)]

        # 전문 에이전트 생성
        analyst = SpecialistAgent(
            name="Analyst",
            system_prompt=ANALYST_SYSTEM_PROMPT,
            gemini_client=self.client,
            gemini_tools=self.gemini_tools,
            sessions=self.sessions,
            tools_map=self.tools_map,
        )

        writer = SpecialistAgent(
            name="Writer",
            system_prompt=WRITER_SYSTEM_PROMPT,
            gemini_client=self.client,
        )

        reviewer = SpecialistAgent(
            name="Reviewer",
            system_prompt=REVIEWER_SYSTEM_PROMPT,
            gemini_client=self.client,
        )

        # 오케스트레이터 생성
        self.orchestrator = Orchestrator(
            gemini_client=self.client,
            specialists={
                "analyst": analyst,
                "writer": writer,
                "reviewer": reviewer,
            },
        )

        print("\n[멀티에이전트 초기화 완료]")
        print("  Orchestrator: 작업 분석 & 위임")
        print("  Analyst: 데이터 수집 (MCP 도구 사용)")
        print("  Writer: 문서 작성")
        print("  Reviewer: 품질 검토")

    async def chat_loop(self):
        """대화형 루프."""
        print("\n=== Gemini Multi-Agent System ===")
        print("종료하려면 'quit' 또는 'exit'를 입력하세요.\n")

        while True:
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n종료합니다.")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                print("종료합니다.")
                break

            try:
                response = await self.orchestrator.process(user_input)
                print(f"\n{'=' * 40}")
                print("최종 결과:")
                print(f"{'=' * 40}")
                print(response)
                print()
            except Exception as e:
                print(f"\n[오류] {e}\n")

    async def cleanup(self):
        """모든 MCP 연결을 정리한다."""
        await self._exit_stack.aclose()
