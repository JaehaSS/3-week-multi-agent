import asyncio
import json
import os
from contextlib import AsyncExitStack

from google import genai
from google.genai import types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class GeminiMCPAgent:
    """Gemini SDK를 사용하여 MCP 서버의 도구를 호출하는 에이전트."""

    def __init__(self, config_path: str = "config.json"):        
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY 환경변수를 설정해주세요.")

        self.client = genai.Client(api_key=api_key)
        self.config_path = config_path
        self.sessions: dict[str, ClientSession] = {}
        self.tools_map: dict[str, str] = {}  # tool_name -> server_name
        self.gemini_tools: list[types.Tool] = []
        self._exit_stack = AsyncExitStack()

    async def connect(self):
        """config.json의 모든 MCP 서버에 연결하고 도구를 수집한다."""
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        servers = config.get("mcpServers", {})
        all_declarations = []

        for server_name, server_config in servers.items():
            command = server_config["command"]
            args = server_config.get("args", [])
            config_env = server_config.get("env", {})

            # 기존 환경변수에 config의 env를 병합 (PATH 등 유지)
            merged_env = {**os.environ, **config_env, "UV_LINK_MODE": "copy"}

            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=merged_env,
            )

            # stdio 클라이언트 연결
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            await session.initialize()

            self.sessions[server_name] = session

            # 도구 목록 가져오기
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

    async def query(self, user_input: str) -> str:
        """사용자 쿼리를 Gemini에 보내고 MCP 도구 호출을 처리한다."""
        messages = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=user_input)],
            )
        ]

        for iteration in range(10):
            response = self.client.models.generate_content(
                model="gemini-2.5-flash",
                contents=messages,
                config=types.GenerateContentConfig(
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
                # 최종 텍스트 응답 반환
                texts = [p.text for p in parts if hasattr(p, "text") and p.text]
                return "\n".join(texts) if texts else "(빈 응답)"

            # 모델 응답을 대화에 추가
            messages.append(candidate.content)

            # 각 함수 호출을 MCP를 통해 실행
            tool_results = []
            for part in function_calls:
                fc = part.function_call
                print(f"  [도구 호출] {fc.name}({dict(fc.args)})")

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

            # 도구 결과를 대화에 추가
            messages.append(types.Content(role="user", parts=tool_results))

        return "[경고] 최대 반복 횟수(10)에 도달했습니다."

    async def chat_loop(self):
        """대화형 루프를 실행한다."""
        print("\n=== Gemini MCP Agent ===")
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
                response = await self.query(user_input)
                print(f"\nGemini: {response}\n")
            except Exception as e:
                print(f"\n[오류] {e}\n")

    async def cleanup(self):
        """모든 MCP 연결을 정리한다."""
        await self._exit_stack.aclose()
