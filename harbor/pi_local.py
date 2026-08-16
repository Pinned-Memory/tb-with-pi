"""Harbor agent: pi driven by a local vLLM server on the host.

Harbor's stock ``pi`` adapter (``harbor.agents.installed.pi:Pi``) installs pi in
the sandbox and runs it with ``--provider/--model``, but pi resolves providers
from ``~/.pi/agent/models.json`` and has no base-URL environment variable for
OpenAI-compatible endpoints. So a local server is unreachable from the sandbox
unless that file exists inside it.

This subclass writes the file during install, and optionally installs the
subagent substrate (``--ak subagents=true``). Everything else -- version
pinning, resume, JSON event parsing, token accounting -- is inherited.

Usage:

    harbor run \\
      -d terminal-bench/terminal-bench-2-1 \\
      -a substrate.harbor.pi_local:PiLocal \\
      -m local-vllm/Qwen/Qwen3.8-27B \\
      --ak base_url=http://172.17.0.1:8000/v1 \\
      --allow-agent-host 172.17.0.1 \\
      -e docker

The sandbox reaches the host through the docker bridge gateway (172.17.0.1 by
default; check ``ip route | grep docker0``). ``--allow-agent-host`` is required
because Harbor firewalls the agent phase -- without it the sandbox cannot open
the connection regardless of what models.json says.

Add ``--ak subagents=true`` to also install pi-subagents (pinned), giving the
agent a delegation tool with builtin subagents. Off by default so that the bare
harness stays the baseline and the substrate is the variable being measured.
"""

import json
import shlex
from pathlib import Path
from typing import override

from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment

# The docker bridge gateway: how a container addresses the host by default.
DEFAULT_BASE_URL = "http://172.17.0.1:8000/v1"

# Provider slug in models.json. Must match the prefix of the -m model name, since
# Pi.run() derives --provider from everything before the first "/".
PROVIDER = "local-vllm"

# This substrate's pi assets (extensions/, models.json): substrate/pi/.
SUBSTRATE_PI_DIR = Path(__file__).resolve().parent.parent / "pi"

# Delegation harness installed with --ak subagents=true. Pinned: an unpinned
# install could change mid-sweep when upstream releases.
PI_SUBAGENTS_VERSION = "0.50.0"


class PiLocal(Pi):
    """pi, pointed at an OpenAI-compatible server outside the sandbox."""

    def __init__(
        self,
        *args,
        base_url: str = DEFAULT_BASE_URL,
        api_key: str = "local",
        context_window: int = 262144,
        max_tokens: int = 32768,
        reasoning: bool = True,
        subagents: bool = False,
        capture: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._base_url = base_url
        self._api_key = api_key
        self._context_window = int(context_window)
        self._max_tokens = int(max_tokens)
        self._reasoning = _as_bool(reasoning)
        self._subagents = _as_bool(subagents)
        self._capture = _as_bool(capture)

    def _model_id(self) -> str:
        """The model id as pi knows it: everything after the provider slug."""
        if not self.model_name or "/" not in self.model_name:
            raise ValueError(
                "Model name must be 'provider/model_id', e.g. "
                f"'{PROVIDER}/Qwen/Qwen3.8-27B'"
            )
        return self.model_name.split("/", 1)[1]

    def _models_json(self) -> str:
        return json.dumps(
            {
                "providers": {
                    PROVIDER: {
                        "name": "Local vLLM",
                        "baseUrl": self._base_url,
                        "api": "openai-completions",
                        "apiKey": self._api_key,
                        "compat": {
                            # vLLM's OpenAI server rejects the `developer` role
                            # and ignores `reasoning_effort`; Qwen3 toggles
                            # thinking through chat_template_kwargs instead.
                            "supportsDeveloperRole": False,
                            "supportsReasoningEffort": False,
                            "supportsStore": False,
                            "maxTokensField": "max_tokens",
                            "thinkingFormat": "qwen-chat-template",
                            "supportsUsageInStreaming": True,
                            "supportsStrictMode": False,
                            "supportsOpenAIGrammarTools": False,
                        },
                        "models": [
                            {
                                "id": self._model_id(),
                                "name": self._model_id(),
                                "reasoning": self._reasoning,
                                "input": ["text"],
                                "contextWindow": self._context_window,
                                "maxTokens": self._max_tokens,
                                "cost": {
                                    "input": 0,
                                    "output": 0,
                                    "cacheRead": 0,
                                    "cacheWrite": 0,
                                },
                            }
                        ],
                    }
                }
            },
            indent=2,
        )

    def _install_subagents_command(self) -> str:
        """Install pi-subagents (third-party) as the delegation harness.

        Replaces the earlier setup built on pi's example extension plus this
        substrate's own agent definitions. pi-subagents ships its own builtin
        agents (scout, researcher, worker, reviewer, oracle, delegate) and
        loads in ``--print`` mode, so the subagent tool is available on the
        headless path Harbor drives. Pinned for run reproducibility.
        """
        return (
            "set -euo pipefail; "
            ". ~/.nvm/nvm.sh; "
            f"pi install npm:pi-subagents@{PI_SUBAGENTS_VERSION}"
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)

        # Written as the agent user so $HOME resolves to that user's home rather
        # than root's -- pi only reads models.json from its own user's config dir.
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "mkdir -p $HOME/.pi/agent && "
                f"printf '%s' {shlex.quote(self._models_json())} "
                "> $HOME/.pi/agent/models.json && "
                # Fail the install here rather than mid-trial if the endpoint is
                # unreachable: a firewalled or wrong base_url otherwise surfaces
                # as an opaque per-task agent failure.
                f"curl -sf --max-time 20 {shlex.quote(self._base_url)}/models "
                "> /dev/null"
            ),
        )

        if self._subagents:
            await self.exec_as_agent(
                environment, command=self._install_subagents_command()
            )

        if self._capture:
            # Request-capture extension (--ak capture=true): records every
            # provider request body to /logs/agent/pi-capture, which Harbor
            # mounts from the host per trial -- so the corpus lands next to
            # pi.txt in jobs/<job>/<trial>/agent/ with no extra plumbing.
            ext = (
                SUBSTRATE_PI_DIR / "extensions" / "capture-payload" / "index.ts"
            ).read_text()
            await self.exec_as_agent(
                environment,
                command=(
                    "set -euo pipefail; "
                    "mkdir -p $HOME/.pi/agent/extensions/capture-payload && "
                    f"printf '%s' {shlex.quote(ext)} "
                    "> $HOME/.pi/agent/extensions/capture-payload/index.ts"
                ),
            )


def _as_bool(value) -> bool:
    """Coerce a --ak kwarg, which arrives as a string, to a bool."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
