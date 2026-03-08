"""Transcript cleanup for voice-dictated user messages.

Uses a two-stage approach:
1. Heuristic pre-filter (pure Python, no deps) to identify likely voice-dictated messages
2. Batch LLM call via LiteLLM to classify and clean messages in a single pass

The LLM returns structured JSON via response_format schema validation.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .scanner import ChatMessage

if TYPE_CHECKING:
    from .database import Database
    from .scanner import ChatSession

# ---------------------------------------------------------------------------
# Heuristic pre-filter (pure Python, no external deps)
# ---------------------------------------------------------------------------

# Common speech-to-text filler words
_FILLER_WORDS = frozenset(
    {
        "like",
        "um",
        "uh",
        "basically",
        "actually",
        "so",
        "literally",
        "right",
        "okay",
        "well",
    }
)


def compute_voice_score(text: str) -> float:
    """Score 0.0 (definitely typed) to 1.0 (definitely voice-dictated).

    Uses pure-Python heuristics — regex and string operations only.
    No external NLP libraries or GPU required.
    """
    if not text or not text.strip():
        return 0.0

    words = text.split()
    word_count = len(words)

    if word_count < 3:
        return 0.0

    score = 0.0

    # Repeated word sequences (e.g., "by the by the by the")
    repeated = re.findall(r"\b(\w+(?:\s+\w+){0,2})\s+\1\b", text.lower())
    if repeated:
        score += min(0.35, len(repeated) * 0.15)

    # Filler word density
    filler_count = sum(1 for w in words if w.lower().strip(".,!?") in _FILLER_WORDS)
    filler_ratio = filler_count / word_count
    if filler_ratio > 0.08:
        score += min(0.25, filler_ratio * 2)

    # Missing terminal punctuation on longer text
    stripped = text.strip()
    if stripped and stripped[-1] not in ".!?\"')" and word_count > 8:
        score += 0.15

    # Run-on: long text with very few sentence breaks
    sentence_enders = len(re.findall(r"[.!?]", text))
    if word_count > 30 and sentence_enders < 2:
        score += 0.2

    # Low punctuation density
    punct_count = len(re.findall(r"[,;:.!?]", text))
    punct_ratio = punct_count / word_count if word_count > 0 else 0
    if word_count > 15 and punct_ratio < 0.03:
        score += 0.1

    return min(1.0, score)


def session_needs_cleanup(session: ChatSession, threshold: float = 0.3) -> list[int]:
    """Return message indices of user messages likely needing cleanup.

    If no messages exceed the threshold, returns an empty list
    (meaning the LLM call can be skipped entirely).
    """
    candidates = []
    for i, msg in enumerate(session.messages):
        if msg.role == "user" and msg.content:
            score = compute_voice_score(msg.content)
            if score >= threshold:
                candidates.append(i)
    return candidates


# ---------------------------------------------------------------------------
# Batch LLM cleanup
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "github_copilot/gpt-4o-mini"

_SYSTEM_PROMPT = """\
You are a transcript cleanup assistant for Copilot chat sessions.
You receive a batch of user messages, some of which may be voice-dictated
(with speech-to-text errors) while others may be cleanly typed.

For each message, you are given context: the AI assistant's response and
reasoning, which show what the user actually meant.

For each message, determine:
1. Whether it appears to be voice-dictated (contains speech artifacts) or typed
2. If voice-dictated, provide a cleaned version

Rules for cleaning:
- Preserve the user's intent and technical meaning exactly
- Fix speech-to-text artifacts: repeated words, homophones, filler words,
  broken sentence structure
- Maintain the user's natural voice and style — don't make it overly formal
- Do NOT add information that wasn't in the original message
- Short messages like "yes", "go ahead", "looks good" are typed — don't touch them
- If typed, set cleaned to the original text unchanged"""


def _build_response_schema(count: int) -> dict:
    """Build the structured output schema for the cleanup response."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "cleanup_results",
            "schema": {
                "type": "object",
                "properties": {
                    "messages": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "is_voice": {"type": "boolean"},
                                "cleaned": {"type": "string"},
                            },
                            "required": ["index", "is_voice", "cleaned"],
                            "additionalProperties": False,
                        },
                        "minItems": count,
                        "maxItems": count,
                    }
                },
                "required": ["messages"],
                "additionalProperties": False,
            },
            "strict": True,
        },
    }


def _get_assistant_context(session: ChatSession, user_msg_index: int) -> str:
    """Get the assistant's response following a user message (truncated)."""
    next_idx = user_msg_index + 1
    if next_idx < len(session.messages) and session.messages[next_idx].role == "assistant":
        content = session.messages[next_idx].content or ""
        return content[:500]
    return ""


def _get_thinking_traces(session: ChatSession, user_msg_index: int) -> str:
    """Get thinking/reasoning traces from the assistant's response."""
    next_idx = user_msg_index + 1
    if next_idx >= len(session.messages):
        return ""

    assistant_msg = session.messages[next_idx]
    traces = []
    for block in assistant_msg.content_blocks:
        if block.kind == "thinking" and block.content:
            traces.append(block.content[:200])
    return " | ".join(traces)[:500] if traces else ""


def _get_tool_summary(session: ChatSession, user_msg_index: int) -> str:
    """Summarize tool invocations from the assistant's response."""
    next_idx = user_msg_index + 1
    if next_idx >= len(session.messages):
        return ""

    assistant_msg = session.messages[next_idx]
    summaries = []
    for tool in assistant_msg.tool_invocations[:5]:
        input_preview = (tool.input or "")[:100]
        summaries.append(f"{tool.name}({input_preview})")
    return "; ".join(summaries) if summaries else ""


def build_batch_prompt(
    session: ChatSession,
    message_indices: list[int],
) -> list[dict]:
    """Build a single LLM prompt containing all target user messages with context.

    Returns the messages list for litellm.completion().
    """
    user_content_parts = []
    for i, msg_idx in enumerate(message_indices):
        msg = session.messages[msg_idx]
        assistant_response = _get_assistant_context(session, msg_idx)
        thinking = _get_thinking_traces(session, msg_idx)
        tools = _get_tool_summary(session, msg_idx)

        part = f'Message {i} (index {msg_idx}):\n  User: "{msg.content}"\n'
        if assistant_response:
            part += f'  Assistant response (first 500 chars): "{assistant_response}"\n'
        if thinking:
            part += f'  Thinking: "{thinking}"\n'
        if tools:
            part += f'  Tools called: "{tools}"\n'
        user_content_parts.append(part)

    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": "\n".join(user_content_parts)},
    ]


@dataclass
class MessageCleanupResult:
    """Result of cleaning a single message."""

    message_index: int
    is_voice: bool
    original: str
    cleaned: str


@dataclass
class SessionCleanupResult:
    """Result of cleaning an entire session."""

    session_id: str
    total_user_messages: int
    detected_voice: int
    cleaned: int
    skipped_clean: int
    failed: int
    results: list[MessageCleanupResult]


def call_cleanup_llm(
    prompt_messages: list[dict],
    model: str,
    expected_count: int,
) -> list[dict]:
    """Call the LLM with structured output schema and return parsed results.

    Raises ImportError if litellm is not installed.
    Raises ValueError if the response doesn't match expected structure.
    """
    try:
        import litellm
    except ImportError as e:
        raise ImportError("litellm is required for transcript cleanup. Install with: pip install copilot-session-tools[llm]") from e

    response = litellm.completion(
        model=model,
        messages=prompt_messages,
        response_format=_build_response_schema(expected_count),
        temperature=0.0,
    )

    content = response.choices[0].message.content
    parsed = json.loads(content)

    results = parsed.get("messages", [])
    if len(results) != expected_count:
        msg = f"Expected {expected_count} results, got {len(results)}"
        raise ValueError(msg)

    return results


def cleanup_session(
    db: Database,
    session_id: str,
    model: str = DEFAULT_MODEL,
    all_messages: bool = False,
    message_index: int | None = None,
    force: bool = False,
    threshold: float = 0.3,
    dry_run: bool = False,
) -> SessionCleanupResult:
    """Orchestrate transcript cleanup for a session.

    Args:
        db: Database instance
        session_id: Session to clean
        model: LiteLLM model identifier
        all_messages: Clean all user messages (skip heuristic detection)
        message_index: Clean only this specific message index
        force: Re-clean already-cleaned messages
        threshold: Voice score threshold for heuristic detection
        dry_run: Preview changes without writing to DB

    Returns:
        SessionCleanupResult with stats and per-message results
    """
    from .markdown_exporter import message_to_markdown

    session = db.get_session(session_id)
    if not session:
        msg = f"Session not found: {session_id}"
        raise ValueError(msg)

    # Determine which messages to process
    if message_index is not None:
        if message_index >= len(session.messages):
            msg = f"Message index {message_index} out of range (session has {len(session.messages)} messages)"
            raise ValueError(msg)
        if session.messages[message_index].role != "user":
            msg = f"Message {message_index} is not a user message"
            raise ValueError(msg)
        target_indices = [message_index]
    elif all_messages:
        target_indices = [i for i, m in enumerate(session.messages) if m.role == "user" and m.content]
    else:
        target_indices = session_needs_cleanup(session, threshold)

    # Filter out already-cleaned messages unless force is set
    if not force:
        target_indices = [i for i in target_indices if session.messages[i].original_content is None]

    total_user = sum(1 for m in session.messages if m.role == "user")

    if not target_indices:
        return SessionCleanupResult(
            session_id=session_id,
            total_user_messages=total_user,
            detected_voice=0,
            cleaned=0,
            skipped_clean=0,
            failed=0,
            results=[],
        )

    # Build batch prompt and call LLM, chunking if needed to stay within token limits
    CHUNK_SIZE = 10  # Max messages per LLM call
    all_llm_results = []
    failed_count = 0

    for chunk_start in range(0, len(target_indices), CHUNK_SIZE):
        chunk_indices = target_indices[chunk_start : chunk_start + CHUNK_SIZE]
        prompt = build_batch_prompt(session, chunk_indices)
        try:
            chunk_results = call_cleanup_llm(prompt, model, len(chunk_indices))
            all_llm_results.extend(chunk_results)
        except Exception as e:
            failed_count += len(chunk_indices)
            all_llm_results.extend({"index": i, "is_voice": True, "cleaned": f"ERROR: {e}"} for i in chunk_indices)

    llm_results = all_llm_results

    # Process results
    cleaned_count = 0
    skipped_count = 0
    message_results = []

    for llm_result in llm_results:
        msg_idx = llm_result["index"]
        is_voice = llm_result["is_voice"]
        cleaned_text = llm_result["cleaned"]
        original_msg = session.messages[msg_idx]

        # Skip error results from failed chunks
        if isinstance(cleaned_text, str) and cleaned_text.startswith("ERROR:"):
            message_results.append(
                MessageCleanupResult(
                    message_index=msg_idx,
                    is_voice=is_voice,
                    original=original_msg.content,
                    cleaned=cleaned_text,
                )
            )
            continue

        result = MessageCleanupResult(
            message_index=msg_idx,
            is_voice=is_voice,
            original=original_msg.content,
            cleaned=cleaned_text,
        )
        message_results.append(result)

        if not is_voice or cleaned_text == original_msg.content:
            skipped_count += 1
            continue

        if not dry_run:
            # Regenerate cached markdown with cleaned content
            cleaned_msg = ChatMessage(
                role=original_msg.role,
                content=cleaned_text,
                timestamp=original_msg.timestamp,
                tool_invocations=original_msg.tool_invocations,
                file_changes=original_msg.file_changes,
                command_runs=original_msg.command_runs,
                content_blocks=original_msg.content_blocks,
                agent_id=original_msg.agent_id,
                agent_display_name=original_msg.agent_display_name,
                agent_nesting_level=original_msg.agent_nesting_level,
            )
            new_cached_md = message_to_markdown(
                cleaned_msg,
                message_number=msg_idx + 1,
                include_diffs=True,
                include_tool_inputs=True,
            )

            _update_message_content(
                db,
                session_id=session_id,
                message_index=msg_idx,
                new_content=cleaned_text,
                original_content=original_msg.content,
                cleanup_model=model,
                cached_markdown=new_cached_md,
            )

        cleaned_count += 1

    detected_voice = sum(1 for r in message_results if r.is_voice)

    return SessionCleanupResult(
        session_id=session_id,
        total_user_messages=total_user,
        detected_voice=detected_voice,
        cleaned=cleaned_count,
        skipped_clean=skipped_count,
        failed=0,
        results=message_results,
    )


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------


def _update_message_content(
    db: Database,
    session_id: str,
    message_index: int,
    new_content: str,
    original_content: str,
    cleanup_model: str,
    cached_markdown: str,
) -> None:
    """Update a message's content in the database, preserving the original."""
    with db._get_connection() as conn:
        conn.execute(
            """
            UPDATE cst_messages
            SET content = ?,
                original_content = ?,
                cleanup_model = ?,
                cached_markdown = ?
            WHERE session_id = ? AND message_index = ?
            """,
            (new_content, original_content, cleanup_model, cached_markdown, session_id, message_index),
        )


def revert_message(db: Database, session_id: str, message_index: int) -> bool:
    """Revert a single message to its original content.

    Returns True if the message was reverted, False if it had no original content.
    """
    from .markdown_exporter import message_to_markdown

    session = db.get_session(session_id)
    if not session:
        msg = f"Session not found: {session_id}"
        raise ValueError(msg)

    if message_index >= len(session.messages):
        msg = f"Message index {message_index} out of range"
        raise ValueError(msg)

    message = session.messages[message_index]
    if message.original_content is None:
        return False

    # Regenerate cached markdown with original content
    reverted_msg = ChatMessage(
        role=message.role,
        content=message.original_content,
        timestamp=message.timestamp,
        tool_invocations=message.tool_invocations,
        file_changes=message.file_changes,
        command_runs=message.command_runs,
        content_blocks=message.content_blocks,
        agent_id=message.agent_id,
        agent_display_name=message.agent_display_name,
        agent_nesting_level=message.agent_nesting_level,
    )
    new_cached_md = message_to_markdown(
        reverted_msg,
        message_number=message_index + 1,
        include_diffs=True,
        include_tool_inputs=True,
    )

    with db._get_connection() as conn:
        conn.execute(
            """
            UPDATE cst_messages
            SET content = original_content,
                original_content = NULL,
                cleanup_model = NULL,
                cached_markdown = ?
            WHERE session_id = ? AND message_index = ?
            """,
            (new_cached_md, session_id, message_index),
        )

    return True


def revert_session(db: Database, session_id: str) -> int:
    """Revert all cleaned messages in a session to their originals.

    Returns the number of messages reverted.
    """
    session = db.get_session(session_id)
    if not session:
        msg = f"Session not found: {session_id}"
        raise ValueError(msg)

    reverted = 0
    for i, msg in enumerate(session.messages):
        if msg.original_content is not None and revert_message(db, session_id, i):
            reverted += 1
    return reverted
