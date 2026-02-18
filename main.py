import asyncio
import sys

from agent import GeminiMCPAgent


async def main():
    config_path = "config.json"
    multi_mode = False

    # CLI 인자 파싱
    for arg in sys.argv[1:]:
        if arg == "--multi":
            multi_mode = True
        else:
            config_path = arg

    if multi_mode:
        from multi_agent import MultiAgentSystem

        system = MultiAgentSystem(config_path=config_path)
        try:
            await system.connect()
            await system.chat_loop()
        finally:
            await system.cleanup()
    else:
        agent = GeminiMCPAgent(config_path=config_path)
        try:
            await agent.connect()
            await agent.chat_loop()
        finally:
            await agent.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
