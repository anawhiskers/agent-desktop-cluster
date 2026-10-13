from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from diy_agent_stack.common import write_json


@unittest.skipUnless(importlib.util.find_spec("mcp"), "Optional MCP SDK is not installed")
class MCPProtocolTests(unittest.TestCase):
    def test_actual_stdio_initialize_list_and_disabled_worker_status(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            worker = root / "worker.json"
            stack = root / "stack.json"
            write_json(worker, dict(schema=1, host="HOST01", state_directory=str(root / "state"),
                       desktop=dict(enabled=False, dedicated_session=True, display=":1", xauthority=str(root / "Xauthority"), session_id="fixture-session"),
                       vault=dict(enabled=False, bw_command="/fixture/bw", runtime_directory="$XDG_RUNTIME_DIR/fixture-vault")))
            write_json(stack, dict(schema=1, hosts=[dict(id="HOST01", transport="local",
                       worker_argv=[sys.executable, "-m", "diy_agent_stack.cli", "worker", "--config", str(worker)])]))
            parameters = StdioServerParameters(command=sys.executable,
                        args=["-m", "diy_agent_stack.cli", "server", "--config", str(stack)],
                        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                             "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})

            async def check():
                async with stdio_client(parameters) as (read, write):
                    async with ClientSession(read, write) as session:
                        initialized = await session.initialize()
                        self.assertTrue(initialized.serverInfo.name)
                        tools = await session.list_tools()
                        names = {tool.name for tool in tools.tools}
                        self.assertEqual(len(names), 22)
                        self.assertNotIn("owner_pause", names)
                        self.assertNotIn("vault_unlock", names)
                        status = await session.call_tool("desktop_status", {"host": "HOST01"})
                        body = json.loads(status.content[0].text)
                        self.assertEqual(body["host"], "HOST01")
                        self.assertFalse(body["enabled"])
                        brief = await session.call_tool("startup_brief", {})
                        self.assertEqual(json.loads(brief.content[0].text)["max_parallel"], 4)

            asyncio.run(check())


if __name__ == "__main__":
    unittest.main()
