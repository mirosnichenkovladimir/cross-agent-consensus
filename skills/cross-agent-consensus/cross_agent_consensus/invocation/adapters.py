"""Player adapter implementations for supervised CAC invocation."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from cross_agent_consensus.io import (
    atomic_write_text,
    compact_json_value,
    content_text_from_message,
    eprint,
)
from cross_agent_consensus.models import (
    AgentInvocation,
    AgentSessionPaths,
    CommandSpec,
    PlayerCapabilities,
)

from .readiness import runtime_command
from .telemetry import agent_event


class GenericCliPlayer:
    def __init__(self, player_id: str = "generic-cli") -> None:
        self.player_id = player_id

    def probe(self, command: list[str]) -> PlayerCapabilities:
        executable_path = shutil.which(command[0]) if command else None
        return PlayerCapabilities(
            player_id=self.player_id,
            executable=executable_path is not None,
            supports_json_events=False,
            supports_resume=False,
            supports_cancel=True,
            prompt_transports=["stdin"],
            output_modes=["raw_stdout"],
            executable_path_or_null=executable_path,
        )

    def build_command(self, invocation: AgentInvocation) -> CommandSpec:
        capabilities = self.probe(invocation.command)
        return CommandSpec(
            argv=invocation.command,
            cwd=invocation.cwd,
            prompt_transport="stdin",
            output_mode="raw_stdout",
            env_allowlist=[],
            executable_probe={
                "executable": capabilities.executable,
                "path": capabilities.executable_path_or_null,
            },
        )

    def extract_final_output(self, paths: AgentSessionPaths) -> Path:
        shutil.copyfile(paths.stdout, paths.final_output)
        return paths.final_output

    def parse_stream_events(
        self,
        stream_name: str,
        data: bytes,
        buffers: dict[str, str],
        invocation: AgentInvocation,
    ) -> list[dict[str, Any]]:
        return []

    def flush_stream_events(
        self,
        buffers: dict[str, str],
        invocation: AgentInvocation,
    ) -> list[dict[str, Any]]:
        return []


class StructuredJsonCliPlayer(GenericCliPlayer):
    def probe(self, command: list[str]) -> PlayerCapabilities:
        capabilities = super().probe(command)
        return PlayerCapabilities(
            player_id=self.player_id,
            executable=capabilities.executable,
            supports_json_events=True,
            supports_resume=False,
            supports_cancel=True,
            prompt_transports=["stdin"],
            output_modes=["stream_json", "raw_stdout"],
            executable_path_or_null=capabilities.executable_path_or_null,
        )

    def build_command(self, invocation: AgentInvocation) -> CommandSpec:
        spec = super().build_command(invocation)
        spec.output_mode = "stream_json" if self.command_requests_json(invocation.command) else "raw_stdout"
        return spec

    def command_requests_json(self, command: list[str]) -> bool:
        return False

    def parse_stream_events(
        self,
        stream_name: str,
        data: bytes,
        buffers: dict[str, str],
        invocation: AgentInvocation,
    ) -> list[dict[str, Any]]:
        text = buffers.get(stream_name, "") + data.decode("utf-8", errors="replace")
        parts = text.splitlines(keepends=True)
        if parts and not parts[-1].endswith(("\n", "\r")):
            buffers[stream_name] = parts.pop()
        else:
            buffers[stream_name] = ""
        return [event for line in parts for event in self.parse_stream_line(stream_name, line, invocation)]

    def flush_stream_events(
        self,
        buffers: dict[str, str],
        invocation: AgentInvocation,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for stream_name, pending in list(buffers.items()):
            if pending:
                events.extend(self.parse_stream_line(stream_name, pending, invocation))
                buffers[stream_name] = ""
        return events

    def parse_stream_line(
        self,
        stream_name: str,
        line: str,
        invocation: AgentInvocation,
    ) -> list[dict[str, Any]]:
        text = line.strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            if self.looks_like_waiting_for_input(text):
                return [agent_event(invocation, "waiting_for_input", stream=stream_name, message=text[:500])]
            return []
        if not isinstance(payload, dict):
            return []
        native_type = self.native_event_type(payload)
        normalized_type = self.normalized_event_type(payload, native_type)
        event_type = "waiting_for_input" if normalized_type == "waiting_for_input" else "agent_event"
        event = agent_event(
            invocation,
            event_type,
            stream=stream_name,
            native_type=native_type,
            normalized_type=normalized_type,
            native_event=compact_json_value(payload),
        )
        return [event]

    def native_event_type(self, payload: dict[str, Any]) -> str:
        msg = payload.get("msg")
        if isinstance(msg, dict) and msg.get("type"):
            return str(msg.get("type"))
        if payload.get("type"):
            return str(payload.get("type"))
        if payload.get("event"):
            return str(payload.get("event"))
        return "unknown"

    def normalized_event_type(self, payload: dict[str, Any], native_type: str) -> str:
        lowered = native_type.lower()
        if "tool" in lowered and any(token in lowered for token in ["call", "use", "begin", "start"]):
            return "tool_call"
        if "tool" in lowered and any(token in lowered for token in ["result", "output", "end", "finish"]):
            return "tool_result"
        if "permission" in lowered or "input" in lowered:
            return "waiting_for_input"
        if lowered in {"result", "task_complete", "completed", "complete"}:
            return "final"
        if "assistant" in lowered or "agent_message" in lowered or "message" in lowered:
            return "message"
        return "runtime"

    def looks_like_waiting_for_input(self, text: str) -> bool:
        lowered = text.lower()
        return any(token in lowered for token in ["permission", "approval", "press enter", "continue?", "login"])

    def extract_final_output(self, paths: AgentSessionPaths) -> Path:
        text = self.extract_text_from_jsonl(paths.stdout)
        if text is None:
            return super().extract_final_output(paths)
        atomic_write_text(paths.final_output, text.rstrip() + "\n")
        return paths.final_output

    def extract_text_from_jsonl(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        parts: list[str] = []
        final_candidates: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            final_text = self.extract_final_text(payload)
            if final_text:
                final_candidates.append(final_text)
                continue
            delta = self.extract_delta_text(payload)
            if delta:
                parts.append(delta)
        if final_candidates:
            return final_candidates[-1]
        if parts:
            return "".join(parts)
        return None

    def extract_final_text(self, payload: dict[str, Any]) -> str | None:
        result = payload.get("result")
        if isinstance(result, str):
            return result
        item = payload.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text:
                return text
        message = payload.get("message")
        if isinstance(message, dict):
            content_text = content_text_from_message(message)
            if content_text:
                return content_text
        msg = payload.get("msg")
        if isinstance(msg, dict):
            for key in ["last_agent_message", "message", "text", "result", "output"]:
                value = msg.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    def extract_delta_text(self, payload: dict[str, Any]) -> str | None:
        msg = payload.get("msg")
        if isinstance(msg, dict):
            msg_type = str(msg.get("type") or "")
            if "agent_message" in msg_type or "assistant" in msg_type:
                for key in ["delta", "text", "message"]:
                    value = msg.get(key)
                    if isinstance(value, str) and value:
                        return value
        delta = payload.get("delta")
        if isinstance(delta, str):
            return delta
        return None


class ClaudeCliPlayer(StructuredJsonCliPlayer):
    def __init__(self) -> None:
        super().__init__("claude-cli")

    def command_requests_json(self, command: list[str]) -> bool:
        for index, arg in enumerate(command):
            if arg == "--output-format" and index + 1 < len(command) and command[index + 1] == "stream-json":
                return True
            if arg == "--output-format=stream-json":
                return True
        return False

    def normalized_event_type(self, payload: dict[str, Any], native_type: str) -> str:
        if native_type == "assistant":
            return "message"
        if native_type == "result":
            return "final"
        if native_type == "stream_event":
            event = payload.get("event")
            event_type = str(event.get("type") or "") if isinstance(event, dict) else ""
            if event_type in {
                "message_start",
                "message_delta",
                "message_stop",
                "content_block_start",
                "content_block_delta",
                "content_block_stop",
            }:
                return "message"
            if "tool" in event_type and any(token in event_type for token in ["start", "call", "use"]):
                return "tool_call"
            if "tool" in event_type and any(token in event_type for token in ["stop", "result", "output"]):
                return "tool_result"
        return super().normalized_event_type(payload, native_type)


class CodexCliPlayer(StructuredJsonCliPlayer):
    def __init__(self) -> None:
        super().__init__("codex-cli")

    def command_requests_json(self, command: list[str]) -> bool:
        return "--json" in command

    def normalized_event_type(self, payload: dict[str, Any], native_type: str) -> str:
        item = payload.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or "")
            status = str(item.get("status") or "")
            if item_type == "agent_message":
                return "message"
            if item_type in {"command_execution", "mcp_tool_call", "tool_call"}:
                if native_type.endswith("started") or status == "in_progress":
                    return "tool_call"
                return "tool_result"
        lowered = native_type.lower()
        if lowered in {"exec_command_begin", "tool_call_begin", "mcp_tool_call_begin"}:
            return "tool_call"
        if lowered in {"exec_command_end", "exec_command_output_delta", "tool_call_end", "mcp_tool_call_end"}:
            return "tool_result"
        if lowered in {"task_complete", "turn_complete", "turn.completed"}:
            return "final"
        return super().normalized_event_type(payload, native_type)


class ManualPlayer:
    player_id = "manual"

    def probe(self, command: list[str]) -> PlayerCapabilities:
        return PlayerCapabilities(
            player_id=self.player_id,
            executable=False,
            supports_json_events=False,
            supports_resume=False,
            supports_cancel=False,
            prompt_transports=["manual"],
            output_modes=["manual_handoff"],
            executable_path_or_null=None,
        )


PLAYER_ALIASES: dict[str, str] = {
    "codex": "codex-cli",
    "claude": "claude-cli",
    "deepseek": "deepseek-cli",
    "generic": "generic-cli",
}

_PLAYER_FACTORIES: dict[str, Any] = {
    "manual": ManualPlayer,
    "claude-cli": ClaudeCliPlayer,
    "codex-cli": CodexCliPlayer,
    "generic-cli": lambda: GenericCliPlayer("generic-cli"),
    "deepseek-cli": lambda: GenericCliPlayer("deepseek-cli"),
}


def get_player_adapter(player_id: str) -> GenericCliPlayer | ManualPlayer:
    canonical = PLAYER_ALIASES.get(player_id, player_id)
    factory = _PLAYER_FACTORIES.get(canonical)
    if factory is not None:
        return factory()
    aliases_hint = ", ".join(f"{alias} ({target})" for alias, target in PLAYER_ALIASES.items())
    raise ValueError(
        f"unknown player: {player_id!r}. "
        f"Available: {', '.join(_PLAYER_FACTORIES)}. Aliases: {aliases_hint}"
    )


def capability_payload(capabilities: PlayerCapabilities) -> dict[str, Any]:
    return {
        "schema_version": "cross-agent-consensus-player-capabilities-1",
        "player_id": capabilities.player_id,
        "executable": capabilities.executable,
        "supports_json_events": capabilities.supports_json_events,
        "supports_resume": capabilities.supports_resume,
        "supports_cancel": capabilities.supports_cancel,
        "prompt_transports": capabilities.prompt_transports,
        "output_modes": capabilities.output_modes,
        "executable_path_or_null": capabilities.executable_path_or_null,
    }


def cmd_players_probe(args: Any) -> int:
    try:
        command = runtime_command(args.command)
        adapter = get_player_adapter(args.player)
        capabilities = adapter.probe(command)
        payload = capability_payload(capabilities)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"player: {payload['player_id']}")
            print(f"executable: {payload['executable']}")
            print(f"path: {payload['executable_path_or_null']}")
            print(f"prompt_transports: {', '.join(payload['prompt_transports'])}")
            print(f"output_modes: {', '.join(payload['output_modes'])}")
        return 0 if capabilities.executable or args.player == "manual" else 2
    except Exception as exc:
        eprint(f"error: {exc}")
        return 1

