"""
OpenCode Assistant Tool for Strands Agents.

Wraps the OpenCode CLI in a Strands @tool.  Each invocation runs
``opencode run --format json --auto`` inside an isolated working
directory so that file-level state persists across calls while
avoiding the output-truncation bug present in ``--attach`` mode.
"""

import json
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional
from strands import tool

try:
    from strands import tool as _strands_tool_decorator
except ImportError:  # pragma: no cover
    _strands_tool_decorator = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OPENCODE_EXE: Optional[str] = None

def _find_opencode_exe() -> str:
    """Locate the opencode binary."""
    global _OPENCODE_EXE
    if _OPENCODE_EXE:
        return _OPENCODE_EXE

    opencode_cmd = shutil.which("opencode")
    if opencode_cmd:
        _OPENCODE_EXE = opencode_cmd
        return _OPENCODE_EXE

    # Fallback to known npx path on Windows (matches current environment)
    fallback = (
        r"C:\Users\FL_LPT-837\AppData\Local\npm-cache\_npx\addef32b45109aa1"
        r"\node_modules\opencode-ai\bin\opencode.exe"
    )
    if os.path.exists(fallback):
        _OPENCODE_EXE = fallback
        return _OPENCODE_EXE

    raise RuntimeError(
        "Could not find opencode executable. "
        "Please ensure opencode is installed and on PATH."
    )


# ---------------------------------------------------------------------------
# Session manager
# ---------------------------------------------------------------------------

class OpenCodeSession:
    """Manages an isolated working directory for OpenCode tasks."""

    def __init__(self, base_dir: str):
        self.work_dir = base_dir

    def _build_run_cmd(self, task_description: str) -> List[str]:
        exe = _find_opencode_exe()
        return [
            exe,
            "run",
            "--format", "json",
            "--dangerously-skip-permissions",
            task_description,
        ]

    def _parse_events(self, stdout: str) -> Dict:
        """Parse NDJSON output from opencode run and condense it."""
        texts: List[str] = []
        tool_summaries: List[str] = []
        errors: List[str] = []
        total_tokens = 0
        total_cost = 0.0

        for line in stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            etype = event.get("type")
            part = event.get("part", {})

            if etype == "text":
                text = part.get("text", "")
                if text:
                    texts.append(text)

            elif etype == "tool_use":
                state = part.get("state", {})
                tool_name = part.get("tool", "unknown")
                status = state.get("status", "")

                if status == "completed":
                    inp = state.get("input", {})
                    out = state.get("output", "")
                    title = part.get("title", "")

                    if tool_name == "write":
                        fp = inp.get("filePath", title)
                        tool_summaries.append(f"[file write] {fp}")
                    elif tool_name == "read":
                        fp = inp.get("filePath", title)
                        out_str = str(out)
                        if len(out_str) > 800:
                            out_str = out_str[:400] + "\n... [truncated] ...\n" + out_str[-400:]
                        tool_summaries.append(f"[file read] {fp}\n{out_str}")
                    elif tool_name == "edit":
                        fp = inp.get("filePath", title)
                        tool_summaries.append(f"[file edit] {fp}")
                    elif tool_name == "bash":
                        cmd = inp.get("command", "")
                        out_str = str(out)
                        if len(out_str) > 800:
                            out_str = out_str[:400] + "\n... [truncated] ...\n" + out_str[-400:]
                        tool_summaries.append(f"[bash] {cmd}\n-> {out_str}")
                    else:
                        out_str = str(out)
                        if len(out_str) > 800:
                            out_str = out_str[:400] + "\n... [truncated] ...\n" + out_str[-400:]
                        tool_summaries.append(f"[{tool_name}] {out_str}")

                elif status == "error":
                    err = state.get("error", "Unknown tool error")
                    errors.append(f"[{tool_name}] ERROR: {err}")

            elif etype == "step_finish":
                tokens = part.get("tokens", {})
                total_tokens += tokens.get("total", 0)
                total_cost += tokens.get("cost", 0)

        sections = []

        if texts:
            sections.append("=== RESPONSE ===\n" + "\n".join(texts))

        if tool_summaries:
            sections.append("=== ACTIONS ===\n" + "\n".join(tool_summaries))

        if errors:
            sections.append("=== ERRORS ===\n" + "\n".join(errors))

        sections.append(f"=== USAGE ===\nTokens: {total_tokens} | Cost: ${total_cost:.4f}")

        return {
            "summary": "\n\n".join(sections),
            "texts": texts,
            "tools": tool_summaries,
            "errors": errors,
            "tokens": total_tokens,
            "cost": total_cost,
        }

    def _run_via_subprocess(self, cmd: List[str], timeout: int) -> str:
        """Execute a command directly via subprocess."""
        print(cmd)
        try:
            result = subprocess.run(
                cmd,
                cwd=self.work_dir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as e:
            return (
                f"ERROR: OpenCode task timed out after {timeout}s.\n"
                f"Partial output:\n{e.stdout or ''}"
            )

        if result.returncode != 0 and not result.stdout.strip():
            return (
                f"ERROR: OpenCode run failed (exit {result.returncode}).\n"
                f"STDERR:\n{result.stderr}"
            )

        return result.stdout

    def run(self, task_description: str, timeout: int = 300) -> str:
        """Execute a task in this OpenCode session and return a condensed summary.
        """
        cmd = self._build_run_cmd(task_description)
        stdout = self._run_via_subprocess(cmd, timeout)
        parsed = self._parse_events(stdout)
        return parsed["summary"]      


# ---------------------------------------------------------------------------
# Factory for Strands @tool
# ---------------------------------------------------------------------------

def create_opencode_tool(
    base_dir: str
):
    """
    Create a Strands @tool bound to a specific OpenCode session.

    Args:
        session_key: Unique identifier for this session
            (e.g. "candidate_001_problem_002").
        base_dir: Root directory for isolated working folders.
        agent: Optional Strands agent instance. When provided, command
            execution is delegated to the agent's shell tool so that
            every invocation and result is recorded in the agent's
            conversation history.

    Returns:
        A callable decorated with @tool that the candidate agent can invoke.
    """
    session = OpenCodeSession(base_dir)

    def _opencode_eval_assistant(task_description: str) -> str:
        """
        Invoke the OpenCode coding assistant to perform evaluation tasks.

        This tool gives you access to a powerful coding agent that can:
        - Read and analyze existing code
        - Run commands and tests
        
        Provide the root directory where the code base resides.


        CRITICAL USAGE GUIDELINES:
        1. Be specific and detailed in your task description.
        2. Mention relevant file paths explicitly
        3. Always review the assistant's output before your next step.

        Args:
            base_dir (str): The base/root path of the project that needs to be evaluated.
            task_description (str): A clear, detailed description of what you
                need the evaluation assistant to do.
                Example:
                - "Run the tests in test_solution.py and report any failures."

        Returns:
            str: A condensed summary of what the assistant did, including errors encountered, and the final response.
        """
        return session.run(task_description)
      
    _opencode_assistant = _opencode_eval_assistant
    if _strands_tool_decorator is not None:
        opencode_assistant = _strands_tool_decorator(_opencode_assistant)

    return opencode_assistant