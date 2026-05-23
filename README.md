# Multi-Agent Pipeline Setup

## What this is
A Claude Code orchestrator that runs PO → Architect → PM → Engineer agents in sequence on a software feature, with human approval gates between each phase.

## Prerequisites
- Claude Code installed (`npm install -g @anthropic-ai/claude-code`)
- Git repo initialised
- (Optional) Jira MCP configured for automatic ticket creation

## Setup Steps

### 1. Copy scaffold into your repo
```bash
cp -r project-scaffold/.claude /your/repo/.claude
cp project-scaffold/CLAUDE.md /your/repo/CLAUDE.md
```

### 2. Write your feature request
Edit `.claude/state/feature-request.md` with the feature you want built.

### 3. Start the orchestrator
```bash
cd /your/repo
claude
```
Claude Code will read `CLAUDE.md` and begin the pipeline automatically.

### 4. Follow the gates
The orchestrator will pause at each gate and ask for your approval:
- **Gate 1** — Review requirements in `.claude/state/requirements.md`
- **Gate 2** — Review ADR in `.claude/state/adr.md`
- **Gate 3** — Review task list in `.claude/state/tasks.json`
- **Gate 4** — Review the opened PRs on GitHub

At each gate, reply `APPROVE` to continue or provide feedback to iterate.

### 5. Engineer agents run in parallel
After Gate 3, the orchestrator creates git worktrees automatically:
```
your-repo/           ← main session (orchestrator)
../your-repo-task-001/  ← Engineer agent 1
../your-repo-task-002/  ← Engineer agent 2
...
```
Each Engineer agent opens a PR when done. You review and merge normally.

### 6. Cleanup
After all PRs are merged:
```bash
git worktree prune
```

---

## Resuming After a Restart
State is persisted in `.claude/state/`. If your Claude Code session dies mid-pipeline:
1. Restart Claude Code in the repo root
2. Tell it: *"Resume the pipeline. Requirements/ADR/tasks are already approved, continue from [step]."*
3. It will read the state files and pick up where it left off.

## Optional: Jira / Confluence Integration
If you have Atlassian MCP configured, the PM Agent will create Jira tickets automatically and any agent can read/write Confluence pages.

Use Atlassian's official remote MCP server (Cloud-hosted, OAuth, covers both Jira and Confluence):
```bash
claude mcp add --scope user --transport http atlassian https://mcp.atlassian.com/v1/mcp/authv2
```
Restart Claude Code afterwards. On the first tool call your browser will open for OAuth against your Atlassian Cloud workspace.

> The `https://mcp.atlassian.com/v1/sse` endpoint is being deprecated on 30 June 2026 — use the `/v1/mcp/authv2` HTTP endpoint above.

For Atlassian Server / Data Center (or if you prefer a self-hosted setup), see the community `mcp-atlassian` server, which uses an API token instead of OAuth.


---

## pipeline-status CLI

The `pipeline-status` package provides a CLI command to inspect
the current state of the multi-agent pipeline.

### Installation

```bash
pip install -e .
```

### Usage

Two equivalent invocation forms are supported:

```bash
# Form 1 -- module invocation
python -m pipeline_status

# Form 2 -- direct entry point (after pip install)
pipeline-status
```

Both forms accept an optional `--state-dir` argument:

```bash
pipeline-status --state-dir /path/to/.claude/state
```

### Sample Output

```
Pipeline Status  --  stage: requirements
------------------------------------------------------------
feature-request.md       EXISTS   FILLED  2026-05-23T09:12:00
requirements.md          EXISTS   FILLED  2026-05-23T09:45:31
adr.md                   MISSING  EMPTY   —
tasks.json               MISSING  EMPTY   —
------------------------------------------------------------
[Requirements     -- PO agent output]
```

### Exit Codes

| Code | Meaning |
|------|---------|
| `0`  | All required artefacts for the current stage are present and filled |
| `2`  | State directory is missing or inaccessible |

### NO_COLOR Environment Variable

| Variable | Effect |
|----------|---------|
| `NO_COLOR` | Set to any value (including empty string) to disable ANSI colour output |

By default, colour is emitted only when stdout is an interactive TTY.
Setting `NO_COLOR` (per the no-color.org convention) disables it unconditionally.

```bash
NO_COLOR=1 pipeline-status
```
