# Skills

The one folder for every skill we write, shared or machine-specific (D12, 2026-10-07). Which
machine and which agent program gets a skill is decided in [`../registry.toml`](../registry.toml),
not by where the folder is. `setup.sh --profile-from aibo-server --only wiring` links each one into
`~/.claude/skills` (Claude Code), `~/.config/opencode/skills` (OpenCode), `~/.agents/skills` (cptr's
own list, Codex) and `~/.gemini/config/skills` (agy).

## Ours (this folder)

| Skill | Machines | What it's for |
|---|---|---|
| [`cos-card`](cos-card/SKILL.md) | aibo-linux, aibo-mac | End a reply with a card for the Chief of Staff panel |
| [`agy`](agy/SKILL.md) | all (not agy itself) | Ask Antigravity a one-shot question through `agy-ask` |
| [`ui-check`](ui-check/SKILL.md) | all | Screenshot UI changes at desktop/phone, light/dark, and look |
| [`linux-operator`](linux-operator/SKILL.md) | aibo-linux | Operate aibo-linux: GNOME desktop, its Chrome, services, cptr |
| [`mac-operator`](mac-operator/SKILL.md) | aibo-mac | Operate aibo-mac and its Agent Chrome for routines (1Password, 2FA, Sheets) |
| [`memory`](../memory/skill/SKILL.md) | aibo-linux, aibo-mac | Shared long-term memory (Graphiti). Kept next to the memory service in `../memory/skill` |

## Linked from elsewhere (registry only)

| Skill | Machines | Lives in |
|---|---|---|
| `agent-browser` | all | the npm package (`$(npm root -g)/agent-browser/skills/agent-browser`), updated with `npm i -g agent-browser` |
| `cua-driver` | macOS | `~/.cua-driver/skills/cua-driver`, updated with `cua-driver skills update` |
| `mattpocock-skills` (25 skills, Claude Code plugin) | aibo-linux, aibo-mac | `[plugins.mattpocock-skills]`: wiring adds the marketplace and installs the plugin |

## Adding one

1. Make `skills/<name>/SKILL.md` here (frontmatter `name` + `description`; see the
   `writing-for-agents` skill).
2. Add `[skills.<name>]` to `registry.toml` with `path = "{agent_tools}/skills/<name>"`, plus
   `machines = [...]` or `os = [...]` if it's only for some machines, and `programs = [...]` if it's
   only for some agents.
3. Run wiring on each machine it's for. cptr only re-reads skills when it restarts.
