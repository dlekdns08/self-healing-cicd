"""
LangGraph ReAct 에이전트 — 진단 → 실행 → 검증 루프
"""
from __future__ import annotations

import os
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from agent.prompts import build_system_prompt
from config.safety import SAFETY_CONFIG
from notifier.slack import notify_escalation, notify_resolved, notify_started, request_human_approval
from storage.db import save_attempt
from tools.definitions import TOOLS

MAX_RETRIES = SAFETY_CONFIG["max_retries"]

_LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()


def _build_llm():
    if _LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "llama3"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0,
        ).bind_tools(TOOLS)
    elif _LLM_PROVIDER in ("openai", "openai_compatible"):
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,  # None → 기본 OpenAI, URL 설정 시 LM Studio/vLLM 등 호환
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            temperature=0,
        ).bind_tools(TOOLS)
    else:  # anthropic (기본값)
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            temperature=0,
        ).bind_tools(TOOLS)


llm = _build_llm()


class AgentState(TypedDict):
    run_id: int
    repo: str
    error_info: dict
    logs: str
    messages: Annotated[list[BaseMessage], add_messages]
    attempt_count: int
    resolved: bool
    escalated: bool


# ── 노드 정의 ──────────────────────────────────────────────

def diagnose_node(state: AgentState) -> dict:
    system = build_system_prompt(state["error_info"], state["repo"])
    user_msg = HumanMessage(
        content=f"로그 스니펫:\n\n{state['error_info']['snippet']}"
    )
    messages = [user_msg] if not state["messages"] else state["messages"]
    response: AIMessage = llm.invoke([("system", system)] + messages)
    return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    node = ToolNode(TOOLS)
    result = node.invoke(state)
    attempt = state["attempt_count"] + 1
    save_attempt(
        run_id=state["run_id"],
        attempt=attempt,
        messages=result["messages"],
    )
    return {**result, "attempt_count": attempt}


def validate_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    # 도구 결과에 "SUCCESS" 포함 여부로 간단 판정 (실제론 re-trigger 결과 폴링)
    if isinstance(last, AIMessage) and "SUCCESS" in last.content.upper():
        return {"resolved": True}
    return {}


def escalate_node(state: AgentState) -> dict:
    notify_escalation(
        run_id=state["run_id"],
        repo=state["repo"],
        error_info=state["error_info"],
        attempt_count=state["attempt_count"],
    )
    return {"escalated": True}


# ── 엣지 조건 ──────────────────────────────────────────────

def route_after_validate(state: AgentState) -> str:
    if state.get("resolved"):
        return END
    if state["attempt_count"] >= MAX_RETRIES:
        return "escalate"
    # 아직 tool_call이 남아있으면 도구 실행, 없으면 재진단
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "diagnose"


def route_after_diagnose(state: AgentState) -> str:
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        # 고위험 도구는 human-in-the-loop
        for call in last.tool_calls:
            if call["name"] in SAFETY_CONFIG["require_human_approval_for"]:
                approved = request_human_approval(
                    run_id=state["run_id"],
                    tool_name=call["name"],
                    tool_args=call["args"],
                )
                if not approved:
                    return "escalate"
        return "tools"
    return END


# ── 그래프 조립 ────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("tools", tool_node)
    g.add_node("validate", validate_node)
    g.add_node("escalate", escalate_node)

    g.set_entry_point("diagnose")
    g.add_conditional_edges("diagnose", route_after_diagnose)
    g.add_edge("tools", "validate")
    g.add_conditional_edges("validate", route_after_validate)
    g.add_edge("escalate", END)
    return g.compile()


_graph = build_graph()


async def run_healing_agent(
    run_id: int, repo: str, error_info: dict, logs: str
) -> AgentState:
    initial: AgentState = {
        "run_id": run_id,
        "repo": repo,
        "error_info": error_info,
        "logs": logs,
        "messages": [],
        "attempt_count": 0,
        "resolved": False,
        "escalated": False,
    }
    notify_started(run_id=run_id, repo=repo, error_info=error_info)
    result = await _graph.ainvoke(initial)
    if result.get("resolved"):
        notify_resolved(run_id=run_id, repo=repo, attempt_count=result["attempt_count"])
    return result
