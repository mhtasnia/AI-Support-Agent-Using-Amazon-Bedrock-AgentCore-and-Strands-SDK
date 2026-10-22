"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the project instructions and rubric for guidance.
Work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
import math

from strands import Agent, tool
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory import MemoryClient
from strands.models import BedrockModel
from strands.tools.mcp.mcp_client import MCPClient
from mcp.client.streamable_http import streamable_http_client
import argparse, json
import os, asyncio, boto3
from strands.hooks import (
    HookProvider, AfterInvocationEvent, HookRegistry, MessageAddedEvent,
)
import logging
import uuid
from typing import Dict
from bedrock_agentcore.tools.code_interpreter_client import code_session
from strands_tools.browser import AgentCoreBrowser


logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("CSAI_Agent")

app = BedrockAgentCoreApp()




# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"

try:
    import shutil, pathlib, playwright
    _src = pathlib.Path(playwright.__file__).parent / "driver" / "node"
    _dst = pathlib.Path("/tmp/pw_node")
    shutil.copyfile(_src, _dst)
    os.chmod(_dst, 0o755)
    os.environ["PLAYWRIGHT_NODEJS_PATH"] = str(_dst)
except Exception:
    logger.warning("Could not prepare playwright node", exc_info=True)


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# Replace the placeholder strings with your actual AWS resource values.
# You collected these in the infrastructure setup section of the project instructions.
#
# GATEWAY_URL format: https://<alias>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp
# This starter uses an unsigned MCP connection and therefore assumes the
# project Gateway is configured with the NONE authorizer.
# KB_ID       format: 10-character alphanumeric string from the KB console
# REGION:     your AWS region, e.g. "us-east-1"
# MEMORY_ID   format: shown in the AgentCore Memory console

GATEWAY_URL = "https://customersupportgateway-ggcrjj0oe9.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"   
KB_ID       = "ICBUXDGKZB"          
REGION      = "us-east-1"       
MEMORY_ID   = "CustomerSupportMemory-DxEGvY7DT0"        


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Model and AWS clients
model_id = "global.amazon.nova-2-lite-v1:0"
model = BedrockModel(model_id=model_id)

memory_client = MemoryClient(region_name=REGION)

_bedrock_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=REGION,
)



# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Read the namespace from strategy["namespaceTemplates"][0].
#      For compatibility with older AgentCore responses, fall back to
#      strategy["namespaces"][0] when namespaceTemplates is absent.
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> Dict:
    """Return a dict mapping strategy type to namespace template string."""
    strategies = mem_client.get_memory_strategies(memory_id)
    namespaces = {}

    for strategy in strategies:
        strategy_type = strategy.get("type")
        templates = strategy.get("namespaceTemplates")

        if not templates:
            templates = strategy.get("namespaces", [])

        if strategy_type and templates:
            namespaces[strategy_type] = templates[0]

    return namespaces


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent


class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        self.namespaces = get_namespaces(memory_client, memory_id)

    @staticmethod
    def _text_from_content(content) -> str:
        """Extract plain text from a Strands message content value."""
        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict) and isinstance(
                    block.get("text"), str
                ):
                    parts.append(block["text"])
            return "\n".join(parts).strip()

        return ""

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to a user message."""
        messages = event.agent.messages
        if not messages:
            return

        message = messages[-1]

        if not isinstance(message, dict) or message.get("role") != "user":
            return

        content = message.get("content")

        # Only process plain-text messages, not images or other content.
        if isinstance(content, list) and any(
            not isinstance(block, dict)
            or not isinstance(block.get("text"), str)
            for block in content
        ):
            return

        query = self._text_from_content(content)
        if not query:
            return
        self._original_query = query

        found_memories = []

        for strategy_type, template in self.namespaces.items():
            namespace = template.replace(
                "{actorId}", self.actor_id
            ).replace("{sessionId}", self.session_id)

            try:
                results = self.memory_client.retrieve_memories(
                    self.memory_id,
                    namespace,
                    query,
                    top_k=5,
                )

                for item in results or []:
                    if not isinstance(item, dict):
                        continue

                    # Handle common memory response structures.
                    memory_content = item.get("content", "")
                    if isinstance(memory_content, dict):
                        memory_text = memory_content.get("text", "")
                    elif isinstance(memory_content, str):
                        memory_text = memory_content
                    else:
                        memory_text = ""

                    if not memory_text and isinstance(item.get("memory"), dict):
                        memory_text = item["memory"].get("content", "")

                    if isinstance(memory_text, str) and memory_text.strip():
                        found_memories.append(
                            f"[{strategy_type}] {memory_text.strip()}"
                        )

            except Exception:
                logger.exception(
                    "Memory retrieval failed for strategy %s",
                    strategy_type,
                )

        if found_memories:
            enriched_message = (
                "Customer Context:\n"
                + "\n".join(found_memories)
                + "\n\n"
                + query
            )

            if isinstance(content, str):
                message["content"] = enriched_message
            else:
                message["content"] = [{"text": enriched_message}]

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed user query and assistant response."""
        messages = event.agent.messages

        customer_query = ""
        agent_response = ""

        # Walk backwards to find the latest user query and assistant reply.
        for message in reversed(messages):
            if not isinstance(message, dict):
                continue

            role = message.get("role")
            text = self._text_from_content(message.get("content"))

            if not text:
                continue

            if role == "assistant" and not agent_response:
                agent_response = text
            elif role == "user" and not customer_query:
                customer_query = text

            if customer_query and agent_response:
                break

        if not customer_query or not agent_response:
            return
        customer_query = getattr(self, "_original_query", customer_query)

        try:
            self.memory_client.create_event(
                memory_id=self.memory_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                messages=[
                    (customer_query, "USER"),
                    (agent_response, "ASSISTANT"),
                ],
            )
        except Exception:
            logger.exception("Failed to save support interaction to memory")

    def register_hooks(self, registry: HookRegistry):
        """Register memory retrieval and saving callbacks."""
        registry.add_callback(
            MessageAddedEvent,
            self.retrieve_customer_context,
        )
        registry.add_callback(
            AfterInvocationEvent,
            self.save_support_interaction,
        )



# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for.
    """
    if not KB_ID:
        return "Knowledge base not configured."

    try:
        response = _bedrock_runtime.retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
        )

        results = response.get("retrievalResults", [])

        if not results:
            return "No relevant information was found in the knowledge base."

        chunks = []
        for result in results:
            content = result.get("content", {})
            text = content.get("text", "").strip()

            if text:
                chunks.append(text)

        if not chunks:
            return "No readable text was found in the knowledge base results."

        return "\n\n".join(chunks)

    except Exception as exc:
        logger.exception("Knowledge Base retrieval failed")
        return f"Knowledge Base search failed: {exc}"


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable


@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate a customer's loyalty discount using AgentCore Code Interpreter.
    Use this tool when calculating points redemption, tier discounts,
    final prices, savings, or points earned.

    Args:
        loyalty_points: Customer's current points balance.
        tier: Silver, Gold, or Platinum.
        order_total: Order total in USD.
        product_category: standard, device, or fresh.
    """
    if loyalty_points < 0 or order_total < 0:
        return "Error: loyalty points and order total cannot be negative."

    # JSON encoding safely embeds the supplied values into the code string.
    code = f"""
import json
import math

loyalty_points = {json.dumps(loyalty_points)}
tier = {json.dumps(tier)}
order_total = {json.dumps(order_total)}
product_category = {json.dumps(product_category)}

earn_rates = {{"standard": 1, "device": 2, "fresh": 5}}
tier_rates = {{"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}}

tier_discount_pct = tier_rates.get(tier.title(), 0.00)
earn_rate = earn_rates.get(product_category.lower(), 1)

# Redeem points in blocks of 500, with a maximum redemption
# value equal to 50% of the original order total.
redeemable_points = (loyalty_points // 500) * 500
max_redemption_value = order_total * 0.50

# Assumption: 100 points have a redemption value of $1.
points_redeemed = min(
    redeemable_points,
    math.floor(max_redemption_value * 100 / 500) * 500
)
points_value = points_redeemed / 100

subtotal_after_points = max(0.0, order_total - points_value)
tier_discount = subtotal_after_points * tier_discount_pct
final_total = max(0.0, subtotal_after_points - tier_discount)
total_savings = order_total - final_total

points_earned = math.floor(final_total * earn_rate)
remaining_points = loyalty_points - points_redeemed + points_earned

print(json.dumps({{
    "points_redeemed": points_redeemed,
    "points_value": round(points_value, 2),
    "tier_discount_pct": round(tier_discount_pct * 100, 2),
    "tier_discount": round(tier_discount, 2),
    "final_total": round(final_total, 2),
    "total_savings": round(total_savings, 2),
    "points_earned": points_earned,
    "remaining_points": remaining_points
}}))
"""
  
    try:
        with code_session(REGION) as code_client:
            response = code_client.invoke(
                "executeCode",
                {
                    "code": code,
                    "language": "python",
                    "clearContext": True,
                },
            )

            for event in response["stream"]:
                result = event.get("result")
                if result is not None:
                    return json.dumps(result) if isinstance(
                        result, (dict, list)
                    ) else str(result)

        raise RuntimeError(
            "Code Interpreter returned no result event."
        )

    except Exception:
        logger.exception(
            "Code Interpreter unavailable; using fallback."
        )

        tier_rates = {
            "Silver": 0.00,
            "Gold": 0.10,
            "Platinum": 0.15,
        }
        tier_discount_pct = tier_rates.get(tier.title(), 0.00)
        tier_discount = order_total * tier_discount_pct
        final_total = max(0.0, order_total - tier_discount)

        return json.dumps({
            "fallback": True,
            "points_redeemed": 0,
            "tier_discount_pct": round(
                tier_discount_pct * 100, 2
            ),
            "final_total": round(final_total, 2),
            "total_savings": round(
                order_total - final_total, 2
            ),
            "points_earned": math.floor(
                final_total * {
                    "standard": 1,
                    "device": 2,
                    "fresh": 5,
                }.get(product_category.lower(), 1)
            ),
            "remaining_points": loyalty_points,
        })



# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
def invoke(payload, context=None):
    """Handle one customer-support request."""
    try:
        user_input = payload.get("prompt", "").strip()
        if not user_input:
            return "Please provide a customer-support question in 'prompt'."

        actor_id = payload.get("customer_id") or "anonymous"
        session_id = payload.get("session_id") or str(uuid.uuid4())

        memory_hook = MemoryHook(
            actor_id=actor_id,
            session_id=session_id,
            memory_client=memory_client,
            memory_id=MEMORY_ID,
        )

        agent_core_browser = AgentCoreBrowser(region=REGION)

        tools = [
            search_knowledge_base,
            calculate_loyalty_discount,
            agent_core_browser.browser,
        ]

        gateway_client = MCPClient(
            lambda: streamable_http_client(GATEWAY_URL)
        )

        with gateway_client:
            gateway_tools = gateway_client.list_tools_sync()
            tools.extend(gateway_tools)

            system_prompt = """
            You are a helpful customer-support AI agent.

            Help customers with orders, refunds, returns, and loyalty rewards.
            Use the knowledge-base tool for company policies and product information.
            Use Gateway tools for customer and order information and refund operations.
            Use the browser when live web information is needed.
            Use the loyalty calculator for discount calculations.
            Use relevant customer memories to personalize responses.
            Never invent order details, refund statuses, or company policies.
            If a tool fails, explain the limitation clearly.
            Be polite, concise, and accurate.
            """

            agent = Agent(
                model=model,
                tools=tools,
                hooks=[memory_hook],
                system_prompt=system_prompt,
            )
            response = agent(user_input)


        content = response.get("content", []) if isinstance(response, dict) else []

        if content:
            first_block = content[0]
            if isinstance(first_block, dict):
                return first_block.get("text", str(first_block))

        return str(response)

    except Exception:
        logger.exception("Agent invocation failed")
        return "Sorry, I couldn't complete your request because of a technical issue. Please try again."


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = invoke(json.loads(args.payload))
    print(response)


if __name__ == "__main__":
    app.run()
    
    # main()
