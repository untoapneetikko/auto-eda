"""Local dev entry point — sets WindowsProactorEventLoopPolicy before uvicorn
creates its event loop, enabling subprocess spawning (required by claude-agent-sdk)."""
import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=["backend", "agents"],
        timeout_graceful_shutdown=1,
    )
