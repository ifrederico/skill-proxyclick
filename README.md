# skill-proxyclick

An agent skill for booking visitors in Proxyclick / Eptura Visitor from the command line,
reverse-engineered from the web app on 2026-10-26. Nothing in it is specific to one company:
the company id, timezone, custom fields and visitor types are read from the account at runtime
(`config`), and the examples in the docs use placeholder ids.

```
SKILL.md                 what the agent reads: setup, commands, workflow, pitfalls
proxyclick-SKILL.md      single-file version for a remote agent with no shell: the same
                         workflow as raw HTTP calls, nothing to install
scripts/proxyclick.py    the client (python3, no dependencies)
references/api.md        raw endpoints and payloads, for when the script needs extending
```

## Give it to an agent

Copy or symlink this folder into the agent's skills directory, for example
`~/.claude/skills/proxyclick-visitors`, keeping the folder structure. The frontmatter in
`SKILL.md` is what makes the agent pick it up.

## Credentials

```bash
mkdir -p ~/.config/proxyclick && chmod 700 ~/.config/proxyclick
cat > ~/.config/proxyclick/credentials.env <<'EOF'
PROXYCLICK_EMAIL=you@example.com
PROXYCLICK_PASSWORD=your-password
EOF
chmod 600 ~/.config/proxyclick/credentials.env
```

`PROXYCLICK_EMAIL` / `PROXYCLICK_PASSWORD` environment variables work too and take precedence.
The token cache lands in `~/.config/proxyclick/token.json`.

This talks to the web app's own private API, not the official `api.proxyclick.com` API, so
endpoints can change without notice; `references/api.md` records what was captured and when.

## Smoke test

```bash
python3 scripts/proxyclick.py login
python3 scripts/proxyclick.py config
python3 scripts/proxyclick.py search "Maria"
python3 scripts/proxyclick.py create-visit --first Test --last Person --email you+test@example.com \
    --date tomorrow --start 10:00 --field parking=NO --dry-run
python3 scripts/proxyclick.py visits tomorrow
```
