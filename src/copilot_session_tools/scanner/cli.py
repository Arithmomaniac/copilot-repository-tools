"""GitHub Copilot CLI session parsing."""

import json
from pathlib import Path

import orjson

from copilot_session_tools.scanner import PARSER_VERSION

from .content import _format_tool_display_message, _get_file_metadata
from .git import detect_repository_url
from .models import (
    ChatMessage,
    ChatSession,
    CommandRun,
    ContentBlock,
    ToolInvocation,
)


def _parse_workspace_yaml(session_dir: Path) -> dict[str, str]:
    """Parse a workspace.yaml file from a CLI session directory.

    The workspace.yaml is a simple key-value YAML file maintained by the Copilot CLI:
        id: <session-uuid>
        cwd: <working-directory>
        summary: <session-title>
        created_at: <timestamp>
        ...

    We parse it manually to avoid adding a PyYAML dependency.

    Args:
        session_dir: Path to the CLI session directory containing workspace.yaml.

    Returns:
        Dictionary of key-value pairs from the file, or empty dict on failure.
    """
    workspace_file = session_dir / "workspace.yaml"
    if not workspace_file.exists():
        return {}

    try:
        result: dict[str, str] = {}
        with workspace_file.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Split on first colon only
                if ":" in line:
                    key, _, value = line.partition(":")
                    result[key.strip()] = value.strip()
        return result
    except OSError:
        return {}


class _CliSessionBuilder:
    """Accumulates CLI session events into ChatMessage objects.

    Manages state for building assistant messages from streaming events,
    combining consecutive assistant messages and interleaving tool invocations.
    """

    def __init__(
        self,
        tool_executions: dict,
        subagent_child_tools: dict[str, list[str]] | None = None,
        subagent_failures: dict[str, str] | None = None,
        bg_agent_results: dict[str, str] | None = None,
        bg_agent_id_map: dict[str, str] | None = None,
        subagent_completions: set[str] | None = None,
        subagent_child_structured: dict[str, list[tuple]] | None = None,
        agent_display_names: dict[str, str] | None = None,
    ) -> None:
        self.tool_executions = tool_executions
        self.subagent_child_tools = subagent_child_tools or {}
        self.subagent_failures = subagent_failures or {}
        self.bg_agent_results = bg_agent_results or {}
        self.bg_agent_id_map = bg_agent_id_map or {}
        self.subagent_completions = subagent_completions or set()
        self.subagent_child_structured = subagent_child_structured or {}
        self.agent_display_names = agent_display_names or {}
        self.messages: list[ChatMessage] = []
        self.current_assistant_content_blocks: list[ContentBlock] = []
        self.current_assistant_tool_invocations: list[ToolInvocation] = []
        self.current_assistant_command_runs: list[CommandRun] = []
        self.current_assistant_timestamp: str | None = None
        self.pending_tool_requests: dict[str, dict] = {}

    def flush_assistant_message(self) -> None:
        """Flush accumulated assistant content blocks into a single message."""
        has_content = self.current_assistant_content_blocks or self.current_assistant_tool_invocations or self.current_assistant_command_runs
        if not has_content:
            return

        # Build flat content from content blocks
        text_parts = []
        for block in self.current_assistant_content_blocks:
            if block.kind == "text" and block.content.strip():
                text_parts.append(block.content)
            elif block.kind in ("subagent", "subagent_failed", "subagent_incomplete") and block.content.strip():
                # Include subagent content in flat text for FTS indexing
                text_parts.append(block.content)
        flat_content = "\n\n".join(text_parts)

        self.messages.append(
            ChatMessage(
                role="assistant",
                content=flat_content,
                timestamp=self.current_assistant_timestamp,
                tool_invocations=self.current_assistant_tool_invocations.copy(),
                command_runs=self.current_assistant_command_runs.copy(),
                content_blocks=self.current_assistant_content_blocks.copy(),
            )
        )

        # Reset state
        self.current_assistant_content_blocks = []
        self.current_assistant_tool_invocations = []
        self.current_assistant_command_runs = []
        self.current_assistant_timestamp = None

    def build_tool_invocation(self, tool_call_id: str, tool_name: str, arguments: dict) -> tuple[ToolInvocation | None, CommandRun | None]:
        """Build a ToolInvocation or CommandRun from tool request data."""
        # Get execution result if available
        execution = self.tool_executions.get(tool_call_id, {})
        complete_event = execution.get("complete")
        start_event = execution.get("start")

        result = None
        status = None
        if complete_event:
            complete_data = complete_event.get("data", {})
            status = "success" if complete_data.get("success") else "error"
            result_obj = complete_data.get("result", {})
            if isinstance(result_obj, dict):
                result = result_obj.get("content", "")
            else:
                result = str(result_obj) if result_obj else None

        # Get description and MCP metadata from start event
        description = None
        mcp_server_name = None
        mcp_tool_name = None
        if start_event:
            start_data = start_event.get("data", {})
            start_args = start_data.get("arguments", {})
            description = start_args.get("description")
            mcp_server_name = start_data.get("mcpServerName")
            mcp_tool_name = start_data.get("mcpToolName")
        if not description:
            description = arguments.get("description")

        # Check if this is a shell/powershell command
        if tool_name in ("powershell", "bash", "shell", "run_command"):
            command = arguments.get("command", "")
            return None, CommandRun(
                command=command,
                title=description,
                result=result,
                status=status,
                output=result,
            )
        else:
            # Regular tool invocation
            input_str = None
            if arguments:
                try:
                    input_str = json.dumps(arguments)
                except (TypeError, ValueError):
                    input_str = str(arguments)

            # Build invocation message for inline display
            invocation_message = _format_tool_display_message(
                tool_name,
                arguments,
                description,
                mcp_server_name=mcp_server_name,
                mcp_tool_name=mcp_tool_name,
            )

            return ToolInvocation(
                name=tool_name,
                input=input_str,
                result=result,
                status=status,
                invocation_message=invocation_message,
                source_type="mcp" if mcp_server_name else None,
            ), None

    def add_tool_inline(self, tool_call_id: str, tool_name: str, arguments: dict) -> None:
        """Add a tool invocation inline in the current assistant message."""
        # Handle special meta-tools with pretty formatting
        if tool_name == "report_intent":
            intent_text = arguments.get("intent", arguments.get("description", ""))
            if intent_text:
                self.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="intent",
                        content=intent_text,
                    )
                )
            return

        if tool_name == "skill":
            skill_name = arguments.get("name", arguments.get("skill", ""))
            if skill_name:
                self.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="skill",
                        content=skill_name,
                    )
                )
            return

        if tool_name == "ask_user":
            question = arguments.get("question") or arguments.get("message", "")
            choices = arguments.get("choices", [])
            if question:
                content = f"❓ {question}"
                if choices:
                    choices_text = ", ".join(str(c) for c in choices[:5])  # Limit to 5 choices
                    if len(choices) > 5:
                        choices_text += f", ... (+{len(choices) - 5} more)"
                    content += f"\n   Options: {choices_text}"
                # Look up the user's answer from the tool execution result
                execution = self.tool_executions.get(tool_call_id, {})
                complete_event = execution.get("complete")
                if complete_event:
                    complete_data = complete_event.get("data", {})
                    if complete_data.get("success"):
                        result_obj = complete_data.get("result", {})
                        answer = result_obj.get("content", "") if isinstance(result_obj, dict) else str(result_obj)
                        answer = answer.removeprefix("User responded: ")
                        if answer:
                            content += f"\n   ✅ **Answer:** {answer}"
                    else:
                        content += "\n   ⏭️ *Skipped*"
                self.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="ask_user",
                        content=content,
                        description="user-input",
                    )
                )
            return

        # Skip truly internal tools with no user-visible output
        internal_tools = {
            "read_powershell",
            "read_bash",
        }
        if tool_name in internal_tools:
            return

        # Resolve read_agent agent_id to display name
        if tool_name == "read_agent":
            agent_id = arguments.get("agent_id", "")
            display = self.agent_display_names.get(agent_id, agent_id)
            resolved_msg = f"⏳ Checking agent {display}"
            self.current_assistant_content_blocks.append(ContentBlock(kind="toolInvocation", content=resolved_msg))
            tool_inv, _ = self.build_tool_invocation(tool_call_id, tool_name, arguments)
            if tool_inv:
                tool_inv.invocation_message = resolved_msg
                self.current_assistant_tool_invocations.append(tool_inv)
            return

        tool_inv, cmd_run = self.build_tool_invocation(tool_call_id, tool_name, arguments)

        if cmd_run:
            # Add command run inline as a content block
            cmd_display = cmd_run.title or cmd_run.command
            if len(cmd_display) > 60:
                cmd_display = cmd_display[:57] + "..."
            self.current_assistant_content_blocks.append(
                ContentBlock(
                    kind="toolInvocation",
                    content=f"$ {cmd_run.command}" if cmd_run.command else cmd_display,
                    description=cmd_run.title,
                )
            )
            self.current_assistant_command_runs.append(cmd_run)

        elif tool_inv:
            if tool_name == "task":
                # Don't render task tool inline — the subagent block replaces it
                self.current_assistant_tool_invocations.append(tool_inv)
                return
            # Add tool invocation inline as a content block
            display_text = tool_inv.invocation_message or tool_inv.name
            self.current_assistant_content_blocks.append(
                ContentBlock(
                    kind="toolInvocation",
                    content=display_text,
                    description=tool_inv.name,
                )
            )
            self.current_assistant_tool_invocations.append(tool_inv)


def _parse_cli_jsonl_file(file_path: Path) -> ChatSession | None:
    """Parse a GitHub Copilot CLI JSONL session file.

    CLI sessions are stored as JSONL (JSON Lines) where each line is a JSON object
    representing an event. The event-based format uses types like:
    - session.start: Session initialization with sessionId, copilotVersion, etc.
    - session.info: Info messages (authentication, mcp, folder_trust)
    - session.model_change: Model switching (newModel)
    - session.error: Error events (errorType, message)
    - session.truncation: Context window management events
    - user.message: User prompts with content and attachments
    - system.message: System-level messages
    - assistant.message: Assistant responses with content and toolRequests
    - assistant.turn_start/end: Turn boundaries
    - tool.execution_start/complete: Tool invocation lifecycle
    - tool.user_requested: User-requested tool executions
    - abort: Session/turn abort events

    This function renders CLI sessions similarly to how vscode-copilot-chat renders
    background chats:
    - Consecutive assistant messages are combined into one
    - Tool calls are displayed inline within the assistant message content

    Args:
        file_path: Path to the JSONL file.

    Returns:
        ChatSession object or None if parsing fails.
    """
    try:
        events = []

        with file_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    data = orjson.loads(line)
                    events.append(data)
                except orjson.JSONDecodeError:
                    continue

        if not events:
            return None

        # Extract session metadata from session.start event
        session_id = None
        created_at = None
        session_start_context: dict = {}

        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})

            if event_type == "session.start":
                session_id = event_data.get("sessionId")
                created_at = event_data.get("startTime") or event.get("timestamp")
                # Extract context for workspace info
                context = event_data.get("context", {})
                session_start_context = context
                break  # Only need the first session.start event

        # If no session.start, use file stem as session ID
        if not session_id:
            session_id = file_path.stem

        # Extract workspace from session.start context or folder_trust event
        workspace_path = session_start_context.get("cwd") or session_start_context.get("gitRoot")
        workspace_name = Path(workspace_path).name if workspace_path else None
        requester_username = None
        session_repository = session_start_context.get("repository")  # e.g. "owner/repo"

        for event in events:
            if event.get("type") == "session.info":
                event_data = event.get("data", {})
                info_type = event_data.get("infoType")
                message = event_data.get("message", "")

                if info_type == "folder_trust" and not workspace_path:
                    # Parse "Folder C:\_SRC\ZTS has been added to trusted folders."
                    if message.startswith("Folder ") and " has been added" in message:
                        folder_path = message[7 : message.find(" has been added")]
                        workspace_path = folder_path
                        workspace_name = Path(folder_path).name

                elif info_type == "authentication" and not requester_username and "as user: " in message:
                    # Parse "Logged in with gh as user: Arithmomaniac"
                    requester_username = message.split("as user: ")[-1].strip()

        # Build tool execution map: toolCallId -> (start_data, complete_data, user_requested)
        tool_executions: dict = {}
        # Map parent agent toolCallId -> list of child tool display names
        subagent_child_tools: dict[str, list[str]] = {}
        # Map parent agent toolCallId -> list of (ToolInvocation | None, CommandRun | None, content_blocks)
        subagent_child_structured: dict[str, list[tuple]] = {}
        # Track which subagents failed (toolCallId -> error message)
        subagent_failures: dict[str, str] = {}
        # Track which subagents completed successfully
        subagent_completions: set[str] = set()
        # Map agent-N -> best result text from read_agent (for background agents)
        bg_agent_results: dict[str, str] = {}
        # Map task toolCallId -> agent-N id (parsed from background placeholder)
        bg_agent_id_map: dict[str, str] = {}
        # Map agent-N -> display title (e.g., "Explore Agent: Find auth")
        agent_display_names: dict[str, str] = {}
        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})

            if event_type == "tool.execution_start":
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": False}
                    tool_executions[tool_call_id]["start"] = event
                # Track child tools of subagents
                parent_tcid = event_data.get("parentToolCallId")
                if parent_tcid:
                    tool_name = event_data.get("toolName", "")
                    # Skip internal/meta tools from child tool display
                    if tool_name not in ("report_intent", "read_agent", "read_powershell", "read_bash", "skill", "task"):
                        arguments = event_data.get("arguments", {})
                        display = _format_tool_display_message(tool_name, arguments)
                        if display:
                            subagent_child_tools.setdefault(parent_tcid, []).append(display)
                        # Also store structured data for child tool (toolCallId, toolName, arguments)
                        subagent_child_structured.setdefault(parent_tcid, []).append((tool_call_id, tool_name, arguments))

            elif event_type == "tool.user_requested":
                # User explicitly requested this tool execution
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": True}
                    else:
                        tool_executions[tool_call_id]["user_requested"] = True

            elif event_type == "tool.execution_complete":
                tool_call_id = event_data.get("toolCallId")
                if tool_call_id:
                    if tool_call_id not in tool_executions:
                        tool_executions[tool_call_id] = {"start": None, "complete": None, "user_requested": False}
                    tool_executions[tool_call_id]["complete"] = event
                    # Map task toolCallId -> agent_id from background placeholder
                    result_obj = event_data.get("result", {})
                    result_content = result_obj.get("content", "") if isinstance(result_obj, dict) else ""
                    if result_content.startswith("Agent started in background with agent_id: "):
                        bg_id = result_content.split("agent_id: ", 1)[1].split(".")[0].strip()
                        bg_agent_id_map[tool_call_id] = bg_id
                        # Build display name from task tool arguments
                        start_info = tool_executions[tool_call_id].get("start")
                        if start_info:
                            args = start_info.get("data", {}).get("arguments", {})
                            agent_type = args.get("agent_type", "agent")
                            desc = args.get("description", "")
                            agent_display_names[bg_id] = f"{agent_type}: {desc}" if desc else agent_type
                    # Collect read_agent results for background agents
                    start_info = tool_executions[tool_call_id].get("start")
                    if start_info and start_info.get("data", {}).get("toolName") == "read_agent" and isinstance(result_obj, dict):
                        content = result_content
                        detailed = result_obj.get("detailedContent", "")
                        # Extract result text from content after "Result:\n" header
                        result_from_content = ""
                        if content and "Result:\n" in content:
                            result_from_content = content.split("Result:\n", 1)[-1].strip()
                        # Use the longer of content-after-Result vs detailedContent
                        best = result_from_content if len(result_from_content) > len(detailed) else detailed
                        if best:
                            agent_id = start_info["data"].get("arguments", {}).get("agent_id", "")
                            # Only keep the best (longest) result per agent
                            if len(best) > len(bg_agent_results.get(agent_id, "")):
                                bg_agent_results[agent_id] = best

            elif event_type == "subagent.failed":
                tool_call_id = event_data.get("toolCallId", "")
                error = event_data.get("error") or ""
                if tool_call_id:
                    subagent_failures[tool_call_id] = error

            elif event_type == "subagent.started":
                pass  # agent_display_names built from tool.execution_complete below

            elif event_type == "subagent.completed":
                tool_call_id = event_data.get("toolCallId", "")
                if tool_call_id:
                    subagent_completions.add(tool_call_id)

        # Build messages using VSCode-style rendering:
        # - Process events in order
        # - Combine consecutive assistant messages
        # - Interleave tool invocations inline with content
        builder = _CliSessionBuilder(
            tool_executions,
            subagent_child_tools,
            subagent_failures,
            bg_agent_results,
            bg_agent_id_map,
            subagent_completions,
            subagent_child_structured,
            agent_display_names,
        )

        for event in events:
            event_type = event.get("type", "")
            event_data = event.get("data", {})
            timestamp = event.get("timestamp")

            # Skip events belonging to a subagent — they have parentToolCallId
            if event_data.get("parentToolCallId"):
                continue

            if event_type == "user.message":
                # Flush any pending assistant content before user message
                builder.flush_assistant_message()
                builder.pending_tool_requests.clear()

                content = event_data.get("content", "")
                builder.messages.append(
                    ChatMessage(
                        role="user",
                        content=content,
                        timestamp=timestamp,
                    )
                )

            elif event_type == "system.message":
                # Flush pending assistant content
                builder.flush_assistant_message()
                builder.pending_tool_requests.clear()

                content = event_data.get("content", "")
                if content:
                    builder.messages.append(
                        ChatMessage(
                            role="system",
                            content=content,
                            timestamp=timestamp,
                        )
                    )

            elif event_type in ("assistant.turn_start", "assistant.turn_end"):
                # Turn boundaries are internal to a single user interaction.
                # Do NOT flush or create separate messages - all assistant turns
                # between user messages should be combined into a single message.
                # Just continue accumulating content.
                pass

            elif event_type == "assistant.message":
                # Set timestamp from first assistant message in the sequence
                if builder.current_assistant_timestamp is None:
                    builder.current_assistant_timestamp = timestamp

                content = event_data.get("content", "")
                tool_requests = event_data.get("toolRequests", [])

                # Add any text content first
                if content and content.strip():
                    builder.current_assistant_content_blocks.append(
                        ContentBlock(
                            kind="text",
                            content=content.strip(),
                        )
                    )

                # Store tool requests for processing when execution starts/completes
                for req in tool_requests:
                    tool_call_id = req.get("toolCallId")
                    if tool_call_id:
                        builder.pending_tool_requests[tool_call_id] = req

            elif event_type == "tool.execution_start":
                # Add the tool invocation inline when execution starts
                tool_call_id = event_data.get("toolCallId")
                tool_name = event_data.get("toolName", "unknown")
                arguments = event_data.get("arguments", {})

                # Use stored request data if available, otherwise use start event data
                req = builder.pending_tool_requests.get(tool_call_id, {})
                if not arguments and req:
                    arguments = req.get("arguments", {})
                if tool_name == "unknown" and req:
                    tool_name = req.get("name", tool_name)

                builder.add_tool_inline(tool_call_id, tool_name, arguments)

            elif event_type == "abort":
                # Session or turn was aborted - add as status block
                abort_reason = event_data.get("reason", "unknown")
                builder.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="status",
                        content=f"Aborted: {abort_reason}",
                        description="abort",
                    )
                )

            elif event_type == "session.error":
                # Session encountered an error - add as status block
                error_type = event_data.get("errorType", "unknown")
                error_message = event_data.get("message", "")
                builder.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="status",
                        content=f"Error: {error_message}" if error_message else f"Error: {error_type}",
                        description="error",
                    )
                )

            elif event_type == "session.model_change":
                # Model was changed during session
                new_model = event_data.get("newModel", "unknown")
                builder.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="status",
                        content=f"Switched to {new_model}",
                        description="model-change",  # hyphenated for CSS class
                    )
                )

            elif event_type == "assistant.reasoning":
                # Reasoning content - similar to VS Code thinking blocks
                reasoning_content = event_data.get("content", "")
                if reasoning_content and reasoning_content.strip():
                    builder.current_assistant_content_blocks.append(
                        ContentBlock(
                            kind="thinking",  # Use existing kind for consistency
                            content=reasoning_content.strip(),
                            description="reasoning",
                        )
                    )

            elif event_type == "skill.invoked":
                # Skill was loaded - show name and content summary
                skill_name = event_data.get("name", "unknown")
                skill_content = event_data.get("content", "")
                # Extract description from YAML frontmatter if present
                skill_desc = None
                if skill_content and "description:" in skill_content:
                    for line in skill_content.split("\n"):
                        if line.strip().startswith("description:"):
                            skill_desc = line.split("description:", 1)[1].strip()
                            break
                builder.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="skill",
                        content=f"Loaded skill: {skill_name}",
                        description=skill_desc,
                    )
                )

            elif event_type == "session.compaction_complete":
                # Session was compacted - show checkpoint info
                checkpoint_num = event_data.get("checkpointNumber", 0)
                summary = event_data.get("summaryContent", "")
                # Extract overview section if present
                overview = None
                if "<overview>" in summary and "</overview>" in summary:
                    overview = summary.split("<overview>")[1].split("</overview>")[0].strip()
                    if len(overview) > 200:
                        overview = overview[:197] + "..."
                if not overview:
                    overview = f"Session compacted to checkpoint {checkpoint_num}"
                builder.current_assistant_content_blocks.append(
                    ContentBlock(
                        kind="status",
                        content=overview,
                        description="compaction",
                    )
                )

            # --- Subagent lifecycle events ---
            elif event_type == "subagent.started":
                display_name = event_data.get("agentDisplayName") or event_data.get("agentName", "unknown")
                tool_call_id = event_data.get("toolCallId", "")
                req = builder.pending_tool_requests.get(tool_call_id, {})
                req_args = req.get("arguments", {})
                description = req_args.get("description", "")
                failed = tool_call_id in builder.subagent_failures
                completed = tool_call_id in builder.subagent_completions
                # Title includes status indicator for template
                title = f"{display_name}: {description}" if description else display_name
                if failed:
                    title = f"❌ {title}"
                # Build content: child tool summaries + result or error
                parts: list[str] = []
                for child_display in builder.subagent_child_tools.get(tool_call_id, []):
                    parts.append(f"*{child_display}*")
                if failed:
                    error = builder.subagent_failures[tool_call_id]
                    parts.append(f"**Error:** {error}" if error else "**Error:** Unknown error")
                else:
                    # Get the agent's result — check for background agent placeholder
                    result_text = ""
                    execution = builder.tool_executions.get(tool_call_id, {})
                    complete_event = execution.get("complete")
                    if complete_event:
                        complete_data = complete_event.get("data", {})
                        result_obj = complete_data.get("result", {})
                        if isinstance(result_obj, dict):
                            result_text = result_obj.get("content", "")
                        elif result_obj:
                            result_text = str(result_obj)
                    # Background agents return a placeholder — use read_agent result instead
                    if not result_text or result_text.startswith("Agent started in background"):
                        agent_id = builder.bg_agent_id_map.get(tool_call_id, "")
                        if agent_id:
                            result_text = builder.bg_agent_results.get(agent_id, "")
                        else:
                            result_text = ""
                    if result_text:
                        parts.append(result_text)
                content = "\n\n".join(parts) if parts else "(no output)"
                if failed:
                    kind = "subagent_failed"
                elif completed:
                    kind = "subagent"
                else:
                    kind = "subagent_incomplete"

                # Build structured content_blocks for the subagent interior
                nested_blocks: list[ContentBlock] = []
                nested_tool_invocations: list[ToolInvocation] = []
                nested_command_runs: list[CommandRun] = []
                for child_tcid, child_tool_name, child_args in builder.subagent_child_structured.get(tool_call_id, []):
                    tool_inv, cmd_run = builder.build_tool_invocation(child_tcid, child_tool_name, child_args)
                    display = _format_tool_display_message(child_tool_name, child_args)
                    if tool_inv:
                        nested_tool_invocations.append(tool_inv)
                        nested_blocks.append(ContentBlock(kind="toolInvocation", content=display or child_tool_name))
                    elif cmd_run:
                        nested_command_runs.append(cmd_run)
                        # Use "$ command" format so metadata matching connects them inline
                        cmd_content = f"$ {cmd_run.command}" if cmd_run.command else (display or cmd_run.command)
                        nested_blocks.append(
                            ContentBlock(
                                kind="toolInvocation",
                                content=cmd_content,
                                description=cmd_run.title,
                            )
                        )
                # Add result text as a text block
                if result_text:
                    nested_blocks.append(ContentBlock(kind="text", content=result_text))
                elif failed:
                    error = builder.subagent_failures.get(tool_call_id, "Unknown error")
                    nested_blocks.append(ContentBlock(kind="text", content=f"**Error:** {error}" if error else "**Error:** Unknown error"))

                block = ContentBlock(kind=kind, content=content, description=title)
                block.content_blocks = nested_blocks
                block.tool_invocations = nested_tool_invocations
                block.command_runs = nested_command_runs
                builder.current_assistant_content_blocks.append(block)

            elif event_type == "subagent.completed":
                display_name = event_data.get("agentDisplayName") or event_data.get("agentName", "unknown")
                tool_call_id = event_data.get("toolCallId", "")
                req = builder.pending_tool_requests.get(tool_call_id, {})
                req_args = req.get("arguments", {})
                description = req_args.get("description", "")
                label = f"{display_name}: {description} — completed" if description else f"{display_name} — completed"
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=label, description="subagent"))

            elif event_type == "subagent.failed":
                display_name = event_data.get("agentDisplayName") or event_data.get("agentName", "unknown")
                tool_call_id = event_data.get("toolCallId", "")
                req = builder.pending_tool_requests.get(tool_call_id, {})
                req_args = req.get("arguments", {})
                description = req_args.get("description", "")
                label = f"{display_name}: {description} — failed" if description else f"{display_name} — failed"
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=label, description="subagent-error"))

            # --- Session lifecycle events ---
            elif event_type == "session.handoff":
                source_type = event_data.get("sourceType") or "unknown"
                repo = event_data.get("repository") or {}
                owner = repo.get("owner") or ""
                name = repo.get("name") or ""
                branch = repo.get("branch") or ""
                if owner and name:
                    repo_info = f" ({owner}/{name} @ {branch})" if branch else f" ({owner}/{name})"
                else:
                    repo_info = ""
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=f"🔄 Session handoff from {source_type}{repo_info}", description="handoff"))

            elif event_type == "session.warning":
                message = (event_data.get("message") or "").strip()
                if message:
                    builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=f"⚠️ {message}", description="warning"))

            elif event_type == "session.mode_changed":
                prev_mode = event_data.get("previousMode") or "unknown"
                new_mode = event_data.get("newMode") or "unknown"
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=f"Mode changed: {prev_mode} → {new_mode}", description="mode-change"))

            elif event_type == "session.context_changed":
                cwd = event_data.get("cwd") or ""
                branch = event_data.get("branch") or ""
                branch_info = f" ({branch})" if branch else ""
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=f"Context changed: {cwd}{branch_info}", description="context-change"))

            elif event_type == "session.plan_changed":
                operation = event_data.get("operation") or "changed"
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=f"Plan {operation}", description="plan-change"))

            elif event_type == "session.task_complete":
                summary = (event_data.get("summary") or "").strip()
                if summary:
                    builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=summary, description="task-complete"))

            elif event_type == "session.shutdown":
                shutdown_type = event_data.get("shutdownType") or "unknown"
                code_changes = event_data.get("codeChanges") or {}
                lines_added = code_changes.get("linesAdded", 0)
                lines_removed = code_changes.get("linesRemoved", 0)
                files_modified = code_changes.get("filesModified") or []
                parts = [f"Session ended ({shutdown_type})"]
                if lines_added or lines_removed:
                    parts.append(f"+{lines_added}/-{lines_removed} lines across {len(files_modified)} files")
                model_metrics = event_data.get("modelMetrics") or {}
                for model_name, metrics in model_metrics.items():
                    metric_obj = metrics or {}
                    requests = (metric_obj.get("requests") or {}).get("count", 0)
                    cost = (metric_obj.get("requests") or {}).get("cost", 0)
                    if requests:
                        parts.append(f"{model_name}: {requests} requests, cost {cost}")
                builder.current_assistant_content_blocks.append(ContentBlock(kind="status", content=" · ".join(parts), description="shutdown"))

            # Skip internal/metadata events (already parsed in metadata extraction or no user content)
            elif event_type in (
                "session.start",  # Parsed above for sessionId, startTime, context
                "session.info",  # Informational (e.g., model confirmations)
                "session.compaction_start",  # Boundary event, paired with compaction_complete
                "session.error",  # Handled above
                # Internal turn boundary markers
                "assistant.turn_start",
                "assistant.turn_end",
                # Internal lifecycle
                "session.resume",
                # Internal token management
                "session.truncation",
                # Internal file notification
                "session.workspace_file_changed",
                # Ephemeral UI events (permission/elicitation/input prompts — not persisted)
                "permission.requested",
                "permission.completed",
                "elicitation.requested",
                "elicitation.completed",
                "user_input.requested",
                "user_input.completed",
                "external_tool.requested",
                "external_tool.completed",
                "command.queued",
                "command.completed",
            ):
                pass

        # Flush any remaining assistant content
        builder.flush_assistant_message()

        messages = builder.messages
        if not messages:
            return None

        # Get file metadata for incremental refresh
        source_file_mtime, source_file_size = _get_file_metadata(file_path)

        # Get updated_at from last event timestamp
        updated_at = events[-1].get("timestamp") if events else None

        # Detect repository URL from session.start context or workspace path
        repository_url = None
        if session_repository:
            repository_url = f"https://github.com/{session_repository}"
        if not repository_url:
            repository_url = detect_repository_url(workspace_path)

        # Determine session title: prefer workspace.yaml summary, fall back to first report_intent
        custom_title = None
        workspace_meta = _parse_workspace_yaml(file_path.parent)
        if workspace_meta.get("summary"):
            custom_title = workspace_meta["summary"]
        if not custom_title:
            # Fall back to first report_intent content block
            for msg in messages:
                for block in msg.content_blocks:
                    if block.kind == "intent" and block.content:
                        custom_title = block.content
                        break
                if custom_title:
                    break

        session = ChatSession(
            session_id=session_id,
            workspace_name=workspace_name,
            workspace_path=workspace_path,
            messages=messages,
            created_at=created_at,
            updated_at=updated_at,
            source_file=str(file_path),
            vscode_edition="cli",  # CLI edition badge
            custom_title=custom_title,
            requester_username=requester_username,
            responder_username=None,
            source_file_mtime=source_file_mtime,
            source_file_size=source_file_size,
            type="cli",
            repository_url=repository_url,
        )
        session.parser_version = PARSER_VERSION
        return session
    except (OSError, Exception):
        return None
