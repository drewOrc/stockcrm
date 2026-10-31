"""CI smoke check: start the MCP server over stdio and make sure it lists tools and answers a call."""
import asyncio
import json
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402


async def main() -> int:
    env = {**os.environ, "PYTHONIOENCODING": "utf-8",
           "STOCKCRM_DB": str(pathlib.Path(tempfile.mkdtemp()) / "ci.db"),
           "STOCKCRM_BOOTSTRAP_KNOWLEDGE": "1", "STOCKCRM_KNOWLEDGE_WRITES": "direct"}
    params = StdioServerParameters(command=sys.executable, args=["-m", "stockcrm.mcp_server"], env=env, cwd=str(ROOT))
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            required = {"get_morning_context", "get_thesis_health", "propose_interaction", "propose_metric",
                        "list_checkpoints", "get_indirect_exposure"}
            missing = required - tools
            if missing:
                print("missing tools:", missing)
                return 1
            res = await s.call_tool("list_templates", {})
            templates = json.loads(res.content[0].text)
            print(f"ok: {len(tools)} tools, {len(templates)} templates")
            return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
