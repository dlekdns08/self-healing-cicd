"""
LangGraph ReAct 에이전트 — 진단 → 실행 → 검증 루프
"""
from __future__ import annotations

import logging
import os
from typing import Annotated, TypedDict

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
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

_REPO_PATH_MAP: dict[str, str] = {
    "api": "/home/api",
    "blog": "/home/blog",
}


def _resolve_repo_path(repo: str) -> str:
    """'owner/repo' → 로컬 절대경로"""
    repo_name = repo.split("/")[-1].lower()
    return _REPO_PATH_MAP.get(repo_name, f"/home/{repo_name}")


def diagnose_node(state: AgentState) -> dict:
    logger.info("[agent] diagnose 시작 | run_id=%s repo=%s error_type=%s attempt=%s",
                state["run_id"], state["repo"], state["error_info"]["type"], state["attempt_count"])
    system = build_system_prompt(state["error_info"], state["repo"])

    if not state["messages"]:
        repo_path = _resolve_repo_path(state["repo"])
        user_content = (
            f"저장소 로컬 경로: {repo_path}\n\n"
            f"전체 로그:\n{state['logs']}\n\n"
            f"에러 스니펫:\n{state['error_info']['snippet']}\n\n"
            f"read_file → apply_patch → security_scan → git_commit_push → re_trigger_pipeline 순서로 진행하세요. "
            f"run_shell은 검증 목적으로만 사용하고 파일 읽기에는 절대 사용하지 마세요."
        )
        messages = [HumanMessage(content=user_content)]
    else:
        messages = state["messages"]

    response: AIMessage = llm.invoke([("system", system)] + messages)
    tool_calls = [c["name"] for c in response.tool_calls] if response.tool_calls else []
    logger.info("[agent] diagnose 완료 | tool_calls=%s", tool_calls)
    return {"messages": [response]}


def tool_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    tool_names = [c["name"] for c in last.tool_calls] if isinstance(last, AIMessage) and last.tool_calls else []
    logger.info("[agent] 도구 실행 | tools=%s", tool_names)
    node = ToolNode(TOOLS)
    result = node.invoke(state)
    attempt = state["attempt_count"] + 1
    logger.info("[agent] 도구 실행 완료 | attempt=%s", attempt)
    save_attempt(
        run_id=state["run_id"],
        attempt=attempt,
        messages=result["messages"],
    )
    return {**result, "attempt_count": attempt}


def validate_node(state: AgentState) -> dict:
    # re_trigger_pipeline 또는 git_commit_push 의 SUCCESS만 진짜 해결로 판정
    for msg in reversed(state["messages"]):
        if not isinstance(msg, ToolMessage):
            continue
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        if "SUCCESS" in content.upper() and any(
            kw in content for kw in ["파이프라인 재실행", "푸시되었습니다"]
        ):
            logger.info("[agent] validate | resolved=True (tool SUCCESS 확인)")
            return {"resolved": True}
    logger.info("[agent] validate | resolved=False")
    return {}


def escalate_node(state: AgentState) -> dict:
    logger.warning("[agent] 에스컬레이션 | run_id=%s attempt=%s", state["run_id"], state["attempt_count"])
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
    logger.info("[agent] 시작 | run_id=%s repo=%s error_type=%s", run_id, repo, error_info["type"])
    notify_started(run_id=run_id, repo=repo, error_info=error_info)
    result = await _graph.ainvoke(initial)
    if result.get("resolved"):
        logger.info("[agent] 해결 완료 | run_id=%s", run_id)
        notify_resolved(run_id=run_id, repo=repo, attempt_count=result["attempt_count"])
    elif result.get("escalated"):
        logger.warning("[agent] 에스컬레이션 완료 | run_id=%s", run_id)
    else:
        logger.info("[agent] 종료 (조치 없음) | run_id=%s", run_id)
    return result
