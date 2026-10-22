
import main
from mcp.client.streamable_http import streamable_http_client
from strands.tools.mcp.mcp_client import MCPClient

client = MCPClient(
    lambda: streamable_http_client(main.GATEWAY_URL)
)

with client:
    tools = client.list_tools_sync()

    for tool in tools:
        print(tool.tool_name)
        print("Description:", getattr(tool, "description", "Not provided"))
        print("-" * 40)
