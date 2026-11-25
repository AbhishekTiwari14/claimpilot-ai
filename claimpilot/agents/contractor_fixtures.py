import json

from langchain_core.messages import AIMessage, ToolMessage


def offline_contractor_turn(messages: list) -> AIMessage:
    """Drive the same tool protocol with deterministic synthetic decisions."""
    seen = {msg.name: json.loads(msg.content) for msg in messages if isinstance(msg, ToolMessage)}
    if "search_contractors" not in seen:
        name, args = "search_contractors", {"limit": 3}
    else:
        candidates = seen["search_contractors"].get("candidate_ids", [])
        if not candidates or "propose_dispatch" in seen:
            return AIMessage(content="")
        name = next(tool for tool in (
            "get_estimated_arrival", "get_dispatch_requirements", "check_availability",
            "get_contractor_performance", "propose_dispatch",
        ) if tool not in seen)
        args = {} if name == "get_dispatch_requirements" else {"contractor_id": candidates[0]}
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"fixture-{len(seen)+1}"}])
