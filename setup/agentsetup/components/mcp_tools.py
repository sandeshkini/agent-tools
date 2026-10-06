"""mcp-tools: the shared publish_artifact / notify MCP server on 127.0.0.1:8009.

macOS without Docker: mcp-tools/install.sh (LaunchAgent; needs PUBLISH_TOKEN and NTFY_TOKEN, taken from
the environment or the profile's [secrets] env_file, never printed). mode = "docker": the compose
service serves it; setup only checks that it answers.
"""
from ..core import CHANGE, GAP, OK, Component, Plan
from .. import host
from ._jobs import describe

LABEL = "com.sandesh.mcp-tools"
NEEDS = ("PUBLISH_TOKEN", "NTFY_TOKEN")
PASS_THROUGH = NEEDS + ("NTFY_TOPIC", "COMPUTER_LABEL", "SOURCE_LABEL")


class McpTools(Component):
    name = "mcp-tools"
    description = "shared MCP server (publish_artifact, notify, ...) on 127.0.0.1:8009 (mcp-tools/install.sh or Docker)"
    order = 20

    def port(self):
        return int(self.cfg.get("port", 8009))

    def docker(self):
        return self.cfg.get("mode") == "docker"

    def detect(self):
        ctx = self.ctx
        f = {"answering": host.http_status(f"http://127.0.0.1:{self.port()}/mcp") is not None}
        if ctx.os == "darwin" and not self.docker():
            sec = ctx.secrets()
            f["jobs"] = host.launch_agents(ctx)["mcp-tools"]
            f["secrets"] = all(sec.get(k) for k in NEEDS)
        f["claude_wired"] = "agent-tools" in host.claude_mcp_servers(ctx)
        oc = host.opencode_config_text(ctx)
        f["opencode"] = oc is not None
        f["opencode_wired"] = bool(oc and '"agent-tools"' in oc)
        return f

    def plan(self, f):
        port = self.port()
        notes = []
        if not f.get("claude_wired"):
            notes.append(f"Claude Code has no `agent-tools` MCP entry (add: claude mcp add --scope user "
                         f"--transport http agent-tools http://127.0.0.1:{port}/mcp)")
        if f.get("opencode") and not f.get("opencode_wired"):
            notes.append("OpenCode's config has no `agent-tools` MCP entry (see mcp-tools/README.md, Wiring)")
        if self.docker() or self.ctx.os != "darwin":
            if f["answering"]:
                return Plan(OK, f"answering on :{port}" + (" (Docker)" if self.docker() else ""), notes=notes)
            if self.docker():
                return Plan(GAP, f"nothing answers on :{port}", human=["start it: docker compose up -d mcp-tools (in agent-tools)"])
            return Plan(GAP, f"nothing answers on :{port}; mcp-tools/install.sh is macOS-only",
                        human=["run the Docker service (mode = \"docker\") or set it up by hand"])
        jobs = f.get("jobs") or []
        loaded = [j for j in jobs if j["loaded"]]
        if loaded and f["answering"]:
            return Plan(OK, f"{describe(loaded[0], str(self.ctx.home))}, answering on :{port}", notes=notes)
        if loaded:
            return Plan(GAP, f"{loaded[0]['label']} is loaded but nothing answers on :{port}",
                        human=["look at its log (/tmp/mcp-tools.log) and restart it"], notes=notes)
        if jobs:
            j = jobs[0]
            return Plan(CHANGE, f"{j['label']} is installed but not loaded", actions=["load it again (launchctl bootstrap)"],
                        data={"do": "load", "label": j["label"], "plist": j["plist"]}, notes=notes)
        if not f.get("secrets"):
            return Plan(GAP, "not installed; needs PUBLISH_TOKEN and NTFY_TOKEN",
                        human=["export PUBLISH_TOKEN and NTFY_TOKEN, or point [secrets] env_file in the profile at a file "
                               "that has them, then re-run"], notes=notes)
        return Plan(CHANGE, "not installed", actions=[f"run mcp-tools/install.sh (LaunchAgent {LABEL}, 127.0.0.1:{port}) "
                                                       "with the tokens from the environment / env_file"],
                    data={"do": "install"}, notes=notes)

    def owned_files(self):
        return [self.ctx.home / f"Library/LaunchAgents/{LABEL}.plist"] if self.ctx.os == "darwin" else []

    def apply(self, plan):
        ctx = self.ctx
        if plan.data.get("do") == "load":
            return host.reload_agent(ctx, plan.data["label"], plan.data["plist"])[0]
        sec = ctx.secrets()
        env = {k: sec.get(k) for k in PASS_THROUGH}
        env["COMPUTER_LABEL"] = env.get("COMPUTER_LABEL") or ctx.section("machine").get("name")
        return ctx.run([ctx.repo / "mcp-tools/install.sh"], extra_env=env) == 0
