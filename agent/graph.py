"""
LangGraph ReAct 에이전트 — 진단 → 실행 → 검증 루프
"""
from __future__ import annotations

import logging
import os
from typing import Annotated, TypedDict

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
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

# ToolNode는 모듈 레벨에서 한 번만 생성 (매 호출마다 재생성하지 않음)
_TOOL_NODE = ToolNode(TOOLS)


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
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            temperature=0,
        ).bind_tools(TOOLS)
    else:  # anthropic (기본값)
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6"),
            temperature=0,
        ).bind_tools(TOOLS)


# LLM은 지연 초기화 — 첫 요청 시 생성 (import 시 API 키 없어도 크래시 안 됨)
_llm: object | None = None


def _get_llm():
    global _llm
    if _llm is None:
        _llm = _build_llm()
    return _llm


class AgentState(TypedDict):
    run_id: int
    repo: str
    error_info: dict
    logs: str
    messages: Annotated[list[BaseMessage], add_messages]
    attempt_count: int
    resolved: bool
    escalated: bool
    # 승인이 필요한 tool_call 정보 (approval_node에서 사용)
    pending_approval_call: dict | None


# ── 저장소 경로 매핑 ────────────────────────────────────────

_REPO_PATH_MAP: dict[str, str] = {
    "api": "/app/api",
    "blog": "/app/blog",
}


def _resolve_repo_path(repo: str) -> str:
    """'owner/repo' → 로컬 절대경로"""
    repo_name = repo.split("/")[-1].lower()
    return _REPO_PATH_MAP.get(repo_name, f"/home/{repo_name}")


# ── 노드 정의 ──────────────────────────────────────────────

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

    response: AIMessage = _get_llm().invoke([SystemMessage(content=system)] + messages)
    tool_calls = [c["name"] for c in response.tool_calls] if response.tool_calls else []
    logger.info("[agent] diagnose 완료 | tool_calls=%s", tool_calls)
    return {"messages": [response], "pending_approval_call": None}


def tool_node(state: AgentState) -> dict:
    last = state["messages"][-1]
    tool_names = [c["name"] for c in last.tool_calls] if isinstance(last, AIMessage) and last.tool_calls else []
    logger.info("[agent] 도구 실행 | tools=%s", tool_names)
    result = _TOOL_NODE.invoke(state)
    attempt = state["attempt_count"] + 1
    logger.info("[agent] 도구 실행 완료 | attempt=%s", attempt)
    save_attempt(
        run_id=state["run_id"],
        attempt=attempt,
        messages=result["messages"],
    )
    return {**result, "attempt_count": attempt}


async def approval_node(state: AgentState) -> dict:
    """고위험 툴 실행 전 Slack을 통해 인간 승인을 비동기로 요청."""
    call = state.get("pending_approval_call")
    if not call:
        logger.warning("[agent] approval_node 호출됐으나 pending_approval_call 없음 — escalate")
        return {"escalated": True}

    logger.info("[agent] 인간 승인 요청 | run_id=%s tool=%s", state["run_id"], call["name"])
    approved = await request_human_approval(
        run_id=state["run_id"],
        tool_name=call["name"],
        tool_args=call["args"],
    )
    if not approved:
        logger.warning("[agent] 승인 거부 | run_id=%s tool=%s", state["run_id"], call["name"])
        return {"escalated": True, "pending_approval_call": None}

    logger.info("[agent] 승인 완료 | run_id=%s tool=%s", state["run_id"], call["name"])
    return {"pending_approval_call": None}


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
    return {"resolved": False}


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

def route_after_diagnose(state: AgentState) -> str:
    last = state["messages"][-1]
    if not (isinstance(last, AIMessage) and last.tool_calls):
        return END

    # 고위험 툴이 있으면 approval 노드로 라우팅 (blocking sleep 없이)
    for call in last.tool_calls:
        if call["name"] in SAFETY_CONFIG["require_human_approval_for"]:
            # pending_approval_call은 diagnose_node 반환값에서 이미 None으로 초기화됨
            # state를 직접 변경할 수 없으므로 approval 노드에서 messages[-1]에서 읽도록 함
            return "approval"

    return "tools"


def route_after_approval(state: AgentState) -> str:
    if state.get("escalated"):
        return "escalate"
    return "tools"


def route_after_validate(state: AgentState) -> str:
    if state.get("resolved"):
        return END
    if state["attempt_count"] >= MAX_RETRIES:
        return "escalate"
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and last.tool_calls:
        return "tools"
    return "diagnose"


# ── 그래프 조립 ────────────────────────────────────────────

def build_graph() -> StateGraph:
    g = StateGraph(AgentState)
    g.add_node("diagnose", diagnose_node)
    g.add_node("approval", approval_node)
    g.add_node("tools", tool_node)
    g.add_node("validate", validate_node)
    g.add_node("escalate", escalate_node)

    g.set_entry_point("diagnose")
    g.add_conditional_edges("diagnose", route_after_diagnose)
    g.add_conditional_edges("approval", route_after_approval)
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
        "pending_approval_call": None,
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