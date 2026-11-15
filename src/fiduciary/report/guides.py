"""The site's guides (1.19.0): static pages a search engine can index, one per question.

The console is one page whose views live after a ``#``, which search engines do not index as
pages of their own. Each guide is a real page at its own address (``/fiduciary/protect/``),
with its own title, description, canonical link and structured data, written as plain HTML
that reads without a script. Each answers one thing people search for, says what to run, says
what it does not do, and links into the live demo and the other guides.

``scripts/build_site.py`` writes each guide as ``<slug>/index.html``, with ``guides.css``
beside the page, and lists them in ``sitemap.xml``.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

__all__ = ["BACKGROUND", "CSS", "GUIDES", "page"]

SITE: Final = "https://crimsondevil0929.github.io/fiduciary/"
REPO: Final = "https://github.com/crimsondevil0929/fiduciary"

BACKGROUND: Final = {
    "destructive-call": [
        (
            "A report of an agent that deleted a production volume and its backups with a "
            "token it found in an unrelated file (Decrypt, April 2026)",
            "https://decrypt.co/365897/ai-agent-deletes-startup-database-9-seconds-founder-says",
        ),
        (
            "A report of a coding agent that deleted a production database it was told to "
            "leave alone (eWeek, July 2025)",
            "https://www.eweek.com/news/replit-ai-coding-assistant-failure/",
        ),
    ],
    "toxic-flow": [
        (
            "A security write-up of a public issue that led an agent to publish a private "
            "repository through an MCP server (May 2025)",
            "https://invariantlabs.ai/blog/mcp-github-vulnerability",
        ),
    ],
    "rug-pull": [
        (
            "A security write-up of an MCP package whose update added a hidden BCC to every "
            "email it sent (September 2025)",
            "https://www.koi.ai/blog/postmark-mcp-npm-malicious-backdoor-email-theft",
        ),
    ],
}
"""The public write-ups each replay reconstructs, linked for background."""


def _replays_guide() -> Mapping[str, Any]:
    """The replays guide, from the run ``scripts/record_replays.py`` recorded."""
    from importlib.resources import files

    recorded = json.loads((files("fiduciary.report") / "replays.json").read_text("utf-8"))
    sections: list[dict[str, Any]] = [
        {
            "h2": "What these are",
            "p": [
                "Each replay reconstructs a pattern from a public incident against stand-in "
                "servers written for it, with a script in the agent's place doing what the "
                "write-ups say the agent did. No real company's systems, name or brand appear "
                "in a replay. Each run protects the stand-ins exactly as fiduciary protect "
                "protects yours, and ends with bundles anyone can verify offline.",
            ],
        },
    ]
    for replay in recorded:
        sections.append(
            {
                "h2": replay["title"],
                "p": [replay["pattern"]],
                "transcript": [(s["actor"], s["text"]) for s in replay["steps"]],
                "after": [
                    f"Caught by {replay['caught']}.",
                    "Bundles: "
                    + "; ".join(f"{name} {verdict}" for name, verdict in replay["bundles"].items())
                    + ".",
                    f"Not caught: {replay['limits']}",
                ],
                "links": BACKGROUND.get(replay["key"], []),
            }
        )
    sections.append(
        {
            "h2": "Run them yourself",
            "code": [
                "pip install interlock-fiduciary",
                "fiduciary replay all --out replays",
                "fiduciary verify-bundle replays/toxic-flow/bundles/web/web.fidb \\",
                "  --charter $(cat replays/toxic-flow/bundles/web/charter.digest) \\",
                "  --keys replays/toxic-flow/bundles/web/pinned-keys.json",
            ],
            "p": [
                "Each replay writes its servers, its protected deployments and its bundles into "
                "the directory, so every step above can be read again from the records.",
            ],
        }
    )
    return {
        "slug": "replays",
        "nav": "Incident replays",
        "title": "AI agent incident replays: what a proxy would have stopped",
        "description": (
            "Three public AI agent incidents, rebuilt against stand-in servers and run through "
            "fiduciary protect: what was held, what was refused, and the bundles."
        ),
        "h1": "Incident replays: three public patterns, run through the proxy",
        "lede": (
            "A destructive call with a found token, a cross-server toxic flow, and a silent "
            "server update. Each is rebuilt from its public write-up and run step by step, and "
            "each step below is what the run printed."
        ),
        "sections": sections,
        "steps": [
            ("Install", "pip install interlock-fiduciary"),
            ("Run the three replays", "fiduciary replay all --out replays"),
            (
                "Verify a bundle offline",
                "fiduciary verify-bundle <bundle> --charter <digest> --keys pinned-keys.json",
            ),
        ],
        "faq": [
            (
                "Are these the real incidents?",
                "No. They are reconstructions of the patterns the public write-ups describe, run "
                "against stand-in servers. The write-ups are linked for background.",
            ),
            (
                "Does a model run in a replay?",
                "No. A script makes the agent's calls, so every run is the same and costs nothing.",
            ),
        ],
        "app": ("Watch a call held in the demo", "../#/run"),
    }


GUIDES: Final[tuple[Mapping[str, Any], ...]] = (
    {
        "slug": "protect",
        "nav": "Secure MCP servers",
        "title": "MCP server security: pin tools and stop rug pulls",
        "description": (
            "Pin every MCP server your agent runs: tool definitions and package versions "
            "locked, risky writes held for a passkey, secrets kept from leaving. One command."
        ),
        "h1": "Secure the MCP servers your agent already runs",
        "lede": (
            "An MCP server can change after you approve it, describe a destructive tool as "
            "read-only, or quietly send what it sees somewhere else. fiduciary protect puts a "
            "proxy in front of each server your client uses, local or remote, without changing "
            "the server."
        ),
        "sections": [
            {
                "h2": "What goes wrong with MCP servers",
                "p": [
                    "A tool's definition is what the model reads before it calls the tool. If "
                    "the definition changes after you looked at it (a new description, a new "
                    "argument, a write that used to be a read), the agent works from something "
                    "nobody approved. People call this a rug pull.",
                    "Packages change too. In 2025 an MCP server published on npm added a "
                    "hidden BCC to every email it sent in one update. Anyone who had it "
                    "installed got the new version on their next start.",
                    "And annotations are only the server's claim. The MCP specification says a "
                    "client must treat them as untrusted unless the server is trusted. A tool "
                    "marked readOnlyHint can still delete things.",
                ],
            },
            {
                "h2": "Protect every server with one command",
                "code": [
                    "pip install interlock-fiduciary",
                    "fiduciary protect --claude-desktop          # says what it would do",
                    "fiduciary protect --claude-desktop --apply  # --cursor, --vscode, --codex",
                    "fiduciary protect --mcp-config .mcp.json --claude-code --apply",
                ],
                "p": [
                    "Each server's entry in the client's file is rewritten to start the proxy, "
                    "which starts a local server, or speaks HTTP to a remote one. Restart the "
                    "client. fiduciary unprotect puts every entry back as it was.",
                ],
            },
            {
                "h2": "Remote servers, and tokens the agent never holds",
                "p": [
                    "A remote server's entry (a URL and its headers) is wrapped the same way. The "
                    "proxy speaks Streamable HTTP to the server, both the 2026-07-28 revision and "
                    "the older one, and its Authorization token leaves the client's file, which "
                    "every program that reads files can read, the agent among them.",
                    "With --move-secrets, each local server's environment values and each remote "
                    "server's token go to the system's own store: the macOS Keychain, the Linux "
                    "Secret Service or the Windows Credential Manager, and a file only you can "
                    "read where none answers. The proxy reads them back when it starts.",
                    "--scan-secrets looks through the project the agent works in for tokens it "
                    "could read in passing, such as a .env file. Each one is given to every "
                    "protected session as a secret before the first call, so no call carries it "
                    "out, even when the agent read it with the client's own file tools.",
                ],
            },
            {
                "h2": "What it pins, and what happens when something changes",
                "ul": [
                    "Each tool's whole definition: name, description, input and output schemas "
                    "and annotations, hashed into a signed binding.",
                    "The server's package version, when the client starts it from a package "
                    "(npx or uvx).",
                    "A tool whose definition moved is withheld before its next call, and "
                    "fiduciary protect --review shows the change field by field. --accept "
                    "signs the new definitions after you have read them.",
                    "A tool that claims to read only, while its own name, description or "
                    "arguments say it writes or sends, is held and flagged.",
                ],
            },
            {
                "h2": "Writes, approvals and what leaves",
                "p": [
                    "Writes go through once. Those that need approval wait for your passkey: "
                    "fiduciary passkey add registers one for every protected server at once. A "
                    "fetch, a search or a message that carries a credential or a private value "
                    "the agent read through any of the client's servers is refused, whole or "
                    "encoded.",
                    "Everything starts in shadow mode. Nothing is refused, and fiduciary shadow "
                    "report lists what would have been. Turn it on when you have read it.",
                ],
            },
            {
                "h2": "What it does not do",
                "ul": [
                    "A server that changes what a tool does behind an unchanged definition and "
                    "version is not caught. A remote server's code cannot be pinned at all, only "
                    "its definitions.",
                    "A token a client gets by OAuth stays with the client: protect holds a token "
                    "given in the entry's headers.",
                    "An agent that can edit the client's file can unwrap a server. protect shows "
                    "which entries are protected.",
                ],
            },
        ],
        "steps": [
            ("Install", "pip install interlock-fiduciary"),
            ("See what it would do", "fiduciary protect --claude-desktop"),
            ("Apply it, then restart the client", "fiduciary protect --claude-desktop --apply"),
            ("Register a passkey for approvals", "fiduciary passkey add"),
            ("Read the shadow report, then enforce", "fiduciary shadow report"),
        ],
        "faq": [
            (
                "Does fiduciary protect change my MCP servers?",
                "No. It changes the client's configuration file so the client starts a proxy, "
                "and the proxy starts the server as before. fiduciary unprotect restores the "
                "file.",
            ),
            (
                "Which clients does it work with?",
                "Claude Desktop, Cursor, Claude Code (a project's .mcp.json), VS Code and Codex "
                "CLI (its config.toml), or any client whose configuration lists mcpServers.",
            ),
            (
                "Does it work with remote MCP servers?",
                "Yes. A remote entry becomes the proxy's command, the proxy speaks HTTP to the "
                "server, and the server's token is kept out of the client's file.",
            ),
            (
                "Does it need a cloud service or an account?",
                "No. It runs on your machine, it is Apache-2.0, and its records stay where its "
                "deployment is.",
            ),
        ],
        "app": ("Watch it hold a call in the demo", "../#/run"),
    },
    {
        "slug": "claude-code",
        "nav": "Claude Code and Cursor",
        "title": "Claude Code and Cursor tool approvals with a passkey",
        "description": (
            "Rules in a prompt get trimmed. Hold a coding agent's risky MCP calls for a passkey, "
            "and put back what a failed turn changed. For Claude Code and Cursor."
        ),
        "h1": "Tool approvals for Claude Code and Cursor that an agent cannot click through",
        "lede": (
            "Agents have deleted production databases and inboxes while their own instructions "
            "said not to. An instruction in a prompt is advice. An approval the agent cannot "
            "give itself is a control."
        ),
        "sections": [
            {
                "h2": "Why rules in the prompt are not enough",
                "p": [
                    "Long sessions get summarized, and a rule can be summarized away. A token "
                    "left in a file lets a call do far more than the task needed. The agent can "
                    "also be talked into things by text it reads: an issue, an email, a web "
                    "page.",
                    "So the check has to sit outside the model, on the calls themselves, and an "
                    "approval has to come from a person, on hardware the agent cannot reach.",
                ],
            },
            {
                "h2": "Set it up for a project",
                "code": [
                    "fiduciary protect --mcp-config .mcp.json --claude-code --apply",
                    "fiduciary passkey add",
                ],
                "p": [
                    "The first command protects every server in the project's .mcp.json and "
                    "adds Claude Code's turn hooks to .claude/settings.local.json. The second "
                    "registers a passkey (Touch ID, Windows Hello or a security key) for "
                    "approvals. For Cursor, use --cursor.",
                ],
            },
            {
                "h2": "What a turn changes",
                "ul": [
                    "Each request a turn's calls make waits in an outbox until the turn ends, "
                    "and is sent only if every call of the turn went through.",
                    "If a call is refused, the turn's earlier changes are put back exactly, "
                    "where the system they changed keeps what undoes them.",
                    "A call that needs approval waits for your passkey. The agent sees that it "
                    "is held, and is told not to call it again.",
                ],
            },
            {
                "h2": "Coding sessions on your own repository",
                "p": [
                    "fiduciary init --claude-code gives each Claude Code session a sandboxed "
                    "worktree. When the session ends, everything it changed is measured as one "
                    "plan and checked against limits you set, before it reaches your branch.",
                ],
            },
        ],
        "steps": [
            (
                "Protect the project's servers",
                "fiduciary protect --mcp-config .mcp.json --claude-code --apply",
            ),
            ("Register a passkey", "fiduciary passkey add"),
            ("Approve held calls in the console", "fiduciary console --accept-approvals --open"),
        ],
        "faq": [
            (
                "Can the agent approve its own call?",
                "Not once a passkey signs for you. Until then the owner's key is a file, and an "
                "agent that can read it could sign with it. fiduciary passkey add retires that "
                "file.",
            ),
            (
                "Does this slow every call down?",
                "Reads go straight through. Writes are staged and checked on your machine before "
                "they are made: nothing goes to a third party, and nothing waits on a person "
                "unless the call needs an approval.",
            ),
        ],
        "app": ("See a refused turn in the demo", "../#/run"),
    },
    {
        "slug": "outbound",
        "nav": "Stop data leaving",
        "title": "Stop AI agents from leaking secrets and private data",
        "description": (
            "An agent can leak a token it read through a URL, a search or an email. Check what "
            "each call carries out against what it read, whole or encoded."
        ),
        "h1": "Keep what your agent read from leaving",
        "lede": (
            "A leak from an agent can look ordinary: a URL with a token in its query string, a "
            "search that carries a customer's address, a pull request into a public repository "
            "after reading a private one."
        ),
        "sections": [
            {
                "h2": "How data leaves through a read",
                "p": [
                    "A read is not only a read. A fetch carries its URL out, a search carries "
                    "its query, an email tool carries its recipients and its body. An agent "
                    "told by a web page to look something up can send what it read anywhere "
                    "those arguments go.",
                ],
            },
            {
                "h2": "What is checked before a call leaves",
                "ul": [
                    "Values. Every credential found in a result (AWS, GitHub, Slack, Stripe, "
                    "OpenAI and Anthropic keys, JWTs, private keys, a password in a connection "
                    "string) and every distinctive value of a private read is kept as a keyed "
                    "digest. A call that carries one, as plain text, URL-encoded, base64 or hex, "
                    "is refused.",
                    "The rule of two. After untrusted input and private data in the same "
                    "session, nothing more is sent out without a person.",
                    "Destinations. After sensitive data, only the hosts, email domains and "
                    "private repositories you list.",
                    "Budgets. At most so many records read and bytes sent in a turn: a bulk "
                    "export trips it.",
                ],
            },
            {
                "h2": "Across servers",
                "p": [
                    "The MCP servers one client starts share what they read. A token read "
                    "through a database server and sent out through a fetch server is caught, "
                    "and each server's own records show it.",
                ],
            },
            {
                "h2": "See it before you enforce it",
                "code": [
                    "$ fiduciary shadow report",
                    "Mandate 2f0c64d1-...",
                    "  1 send judged before leaving: 0 refused.",
                    "  1 send would have been refused if the mandate were enforced:",
                    "    a credential the session had read, in what it sent: 1 send, "
                    "from fetch (1)",
                ],
                "p": [
                    "Each send is recorded with its destinations, its size and the digests it "
                    "carried, never the values, and the offline verifier judges every one "
                    "again.",
                ],
            },
            {
                "h2": "What it does not catch",
                "p": [
                    "It catches an exact value, whole or encoded once. A model that paraphrases "
                    "a secret, splits it across calls, or sends one shorter than eight "
                    "characters is not caught, and neither is data sent through a channel the "
                    "proxy does not serve, such as the agent's own shell.",
                ],
            },
        ],
        "steps": [],
        "faq": [
            (
                "Are the values stored anywhere?",
                "No. Each is kept as an HMAC digest under a key that stays on your machine. The "
                "records hold digests, so a bundle can be shared without the values.",
            ),
            (
                "Does a model decide what is sensitive?",
                "No. Labels come from where data came from (a private repository, a signed-in "
                "server, an open web page) and from credential formats. Nothing is decided by a "
                "model.",
            ),
        ],
        "app": ("Try the verifier on a recorded run", "../#/verify"),
    },
    {
        "slug": "passkeys",
        "nav": "Passkeys for approvals",
        "title": "Passkeys for AI agent approvals (WebAuthn)",
        "description": (
            "Sign an AI agent's approvals with a passkey (Touch ID, Windows Hello or a security "
            "key), so the agent cannot approve its own actions."
        ),
        "h1": "Approve an agent's risky actions with a passkey",
        "lede": (
            "An approval kept in a key file can be read by any program you run, the agent's "
            "tools included. A passkey signs only when its owner touches it, and its private key "
            "never leaves the device."
        ),
        "sections": [
            {
                "h2": "What a passkey signs",
                "p": [
                    "Each approval is a signed document over the exact bytes of what you were "
                    "shown: the ticket, the option you chose, and when it expires. The WebAuthn "
                    "challenge is that document's signing input, so the signature covers it and "
                    "nothing else.",
                    "One touch can approve several protected servers at once: the challenge is "
                    "the root of a hash tree over every document in the batch.",
                ],
            },
            {
                "h2": "Set it up",
                "code": [
                    "fiduciary passkey add",
                    "fiduciary console --accept-approvals --open",
                ],
                "p": [
                    "The console runs on your machine. It shows each held call with what was "
                    "measured, and your browser asks for the passkey when you approve.",
                ],
            },
            {
                "h2": "Two of three, when it matters",
                "p": [
                    "A solo deployment starts with you alone. fiduciary solo add-principals "
                    "adds two people, and from then on two of the three approve anything that "
                    "grants more.",
                ],
            },
            {
                "h2": "What it cannot prove",
                "p": [
                    "A passkey proves that a verified person touched it on a site the charter "
                    "admits. WebAuthn does not display what is being signed, so it cannot prove "
                    "that the person read the page.",
                ],
            },
        ],
        "steps": [
            ("Register a passkey", "fiduciary passkey add"),
            ("Open the approvals console", "fiduciary console --accept-approvals --open"),
            ("Add two more people when you need them", "fiduciary solo add-principals a b"),
        ],
        "faq": [
            (
                "Which authenticators work?",
                "Any WebAuthn passkey with ES256: Touch ID, Windows Hello, an Android or iOS "
                "passkey, or a hardware security key.",
            ),
            (
                "How does an auditor check a passkey signature?",
                "fiduciary verify-bundle checks each signature offline against the charter's "
                "keys, and the browser verifier on this site checks the passkey's own "
                "signature too.",
            ),
        ],
        "app": ("See approvals in the demo", "../#/run"),
    },
    {
        "slug": "verify",
        "nav": "Verify an audit bundle",
        "title": "Offline audit proof for AI agents: verify a bundle",
        "description": (
            "Check what an AI agent did from a signed audit bundle, offline. Fourteen checks, "
            "mapped to SOC 2, the EU AI Act, OWASP's agentic top 10 and AIUC-1."
        ),
        "h1": "Check what an AI agent did, offline",
        "lede": (
            "An audit bundle is a single file: the mandates, approvals, receipts and records of "
            "one agent's work, signed and hash-chained. Anyone with the public keys can check "
            "it on their own machine, without access to your systems."
        ),
        "sections": [
            {
                "h2": "Run the verifier",
                "code": [
                    "fiduciary export-bundle --mandate <id> --ledger-key <key> --out work.fidb",
                    "fiduciary verify-bundle work.fidb --charter <digest> --keys pinned-keys.json",
                ],
                "p": [
                    "It exits 0 when every check passes, and with the code of the first check "
                    "that fails otherwise, so a pipeline can gate on it.",
                ],
            },
            {
                "h2": "The fourteen checks",
                "ul": [
                    "V1 to V3: the bundle's structure, the charter you pinned, and who could "
                    "grant what to whom.",
                    "V4 to V8: each change was judged by the checks its mandate required, every "
                    "receipt is present, the ledger balances, nothing happened outside a "
                    "warrant, and each warrant had its quorum.",
                    "V9 to V12: signatures, anchors, revocation, and time order.",
                    "V13: what each session read, and what it did and sent after.",
                    "V14: every undo put back exactly what its change did.",
                ],
            },
            {
                "h2": "In your browser",
                "p": [
                    "The verify page on this site checks what a browser can on its own, with Web "
                    "Crypto: the container, every file's digest, the hash chain and every "
                    "signature. Nothing is uploaded.",
                ],
            },
            {
                "h2": "Mapped to the frameworks auditors ask about",
                "p": [
                    "Each check's evidence is mapped to OWASP's Top 10 for Agentic "
                    "Applications, SOC 2, the EU AI Act, AIUC-1, ISO 27001, NIST SP 800-53, "
                    "HIPAA, 21 CFR Part 11, SOX and GDPR. The mapping helps an assessment; it is "
                    "not a certification, and an assessor decides whether a control is met.",
                ],
            },
        ],
        "steps": [
            (
                "Export the bundle",
                "fiduciary export-bundle --mandate <id> --ledger-key <key> --out work.fidb",
            ),
            ("Pin the charter and keys you were given separately", "pinned-keys.json"),
            (
                "Run the verifier",
                "fiduciary verify-bundle work.fidb --charter <digest> --keys pinned-keys.json",
            ),
        ],
        "faq": [
            (
                "Does the auditor need access to our systems?",
                "No. The bundle and the public keys are enough, and they can be checked on a "
                "machine with no network.",
            ),
            (
                "Can a bundle be edited without it showing?",
                "An edit breaks a digest, a signature or the chain, and the verifier reports "
                "it. Signed records make tampering visible. They do not prevent it.",
            ),
        ],
        "app": ("Open the verifier", "../#/verify"),
    },
    {
        "slug": "ci",
        "nav": "Agents in CI",
        "title": "AI agents in GitHub Actions without leaked secrets",
        "description": (
            "An agent in a GitHub Actions job reads text anyone can write. Label it untrusted "
            "from the first call, keep secrets from the agent, and refuse workflow edits."
        ),
        "h1": "Run an AI agent in GitHub Actions without handing it your secrets",
        "lede": (
            "An agent that answers issues or reviews pull requests reads text anyone can write, "
            "with tools that can push, comment and read the job's secrets. A line in its prompt "
            "is not a control. fiduciary ci puts its MCP servers behind protected proxies for "
            "the length of the job."
        ),
        "sections": [
            {
                "h2": "What goes wrong",
                "p": [
                    "Public write-ups since late 2025 show the pattern working across several "
                    "coding agents in GitHub Actions: text in an issue or a comment tells the "
                    "agent what to do, and the agent prints secrets into a comment or edits a "
                    "workflow file. The text came from someone outside the project, and the "
                    "agent could not tell.",
                ],
            },
            {
                "h2": "Add it to a workflow",
                "code": [
                    "- uses: crimsondevil0929/fiduciary@v1.20.0",
                    "  id: fiduciary",
                    "  with:",
                    "    step: start",
                    "    mcp-config: .github/agent-mcp.json",
                    "    secrets: GITHUB_TOKEN",
                    "  env:",
                    "    GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}",
                    "",
                    "- uses: anthropics/claude-code-action@v1",
                    "  with:",
                    "    claude_args: >-",
                    "      --mcp-config ${{ steps.fiduciary.outputs.mcp-config }}",
                    "      --disallowedTools Bash",
                    "",
                    "- uses: crimsondevil0929/fiduciary@v1.20.0",
                    "  if: always()",
                    "  with:",
                    "    step: finish",
                ],
                "p": [
                    "The start step reads the secrets from its own environment and hands them to "
                    "the proxy. The agent's step is given only the MCP file the proxy wrote: "
                    "loopback addresses and a token that reaches the proxy and nothing else. The "
                    "finish step writes the summary and uploads the bundles.",
                ],
            },
            {
                "h2": "What it does for each call",
                "ul": [
                    "Untrusted from the start. Issue and pull request text, comments, reviews and "
                    "branch names from anyone the repository does not trust (owners, members and "
                    "collaborators are trusted by default), and anything from a fork, label "
                    "every session before the agent reads a word.",
                    "Secrets kept. The job's secrets live in the proxy's memory alone, and a "
                    "call that carries one is refused, whole or encoded.",
                    "Workflow files refused. A call whose arguments point at a path under "
                    ".github/workflows or .github/actions is refused before it runs.",
                    "Refuse and explain. Nobody approves in CI, so a call the mandate would hold "
                    "is not made, and the job summary says which check stopped which call.",
                    "Proof. Each server's bundle is exported with its pinned keys and verified "
                    "before it is uploaded.",
                ],
            },
            {
                "h2": "What it does not do",
                "ul": [
                    "It governs the tools behind the proxy. An agent with a shell reaches the "
                    "network and the runner's files directly, so give it none: in Claude Code, "
                    "--disallowedTools Bash.",
                    "A local MCP server holds its own token in its environment, which a process "
                    "of the same user with a shell could read. A remote server's token never "
                    "leaves the proxy.",
                    "Approvals are not asked for in CI. A held call waits, and the job ends "
                    "without it.",
                ],
            },
        ],
        "steps": [
            ("List the agent's MCP servers in a file", ".github/agent-mcp.json"),
            ("Start the proxy before the agent", "uses: crimsondevil0929/fiduciary (step: start)"),
            ("Give the agent the proxy's MCP file", "--mcp-config <the start step's output>"),
            ("Finish after it, always", "uses: crimsondevil0929/fiduciary (step: finish)"),
        ],
        "faq": [
            (
                "Which agents does it work with?",
                "Any agent that reads an MCP file over HTTP: Claude Code and its GitHub Action, "
                "Codex and others. The proxy is the same for each.",
            ),
            (
                "Can it post the summary on the pull request?",
                "Yes, with comment: refusals or always on the finish step, and permission to "
                "write issues or pull requests.",
            ),
            (
                "Does the job fail when a call is refused?",
                "Not by default. Set fail-on: refused, or held, on the finish step.",
            ),
        ],
        "app": ("Read the incident replays", "../replays/"),
    },
    _replays_guide(),
)
"""Each guide: its address, its words, and where it leads."""

CSS: Final = """\
:root { --page: #f6f5f1; --surface: #ffffff; --line: #e3e1da; --ink: #121212; --ink-2: #47453f;
  --muted: #84817a; --accent: #087a57; --code: #0f1114; --code-ink: #e7e6e1;
  --sans: system-ui, -apple-system, "Segoe UI", Inter, Roboto, "Helvetica Neue", sans-serif;
  --mono: ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, Consolas, monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --page: #0b0c0d;
  --surface: #111315; --line: rgba(255,255,255,.085); --ink: #ebe9e4; --ink-2: #aeaba4;
  --muted: #7a7770; --accent: #4fd6a5; } }
:root[data-theme="dark"] { --page: #0b0c0d; --surface: #111315; --line: rgba(255,255,255,.085);
  --ink: #ebe9e4; --ink-2: #aeaba4; --muted: #7a7770; --accent: #4fd6a5; }
* { box-sizing: border-box; margin: 0; }
body { background: var(--page); color: var(--ink); font: 16px/1.65 var(--sans);
  -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
.wrap { max-width: 760px; margin: 0 auto; padding: 0 16px; }
.top { border-bottom: 1px solid var(--line); }
.top .wrap { max-width: 1120px; display: flex; align-items: center; gap: 18px; height: 58px;
  flex-wrap: wrap; }
.brand { display: flex; align-items: center; gap: 9px; color: var(--ink); font-weight: 650; }
.top nav { display: flex; gap: 16px; margin-left: auto; font-size: 14px; flex-wrap: wrap; }
.top nav a { color: var(--ink-2); }
main { padding: 56px 0 72px; }
.kicker { font: 500 12px var(--mono); letter-spacing: .08em; text-transform: uppercase;
  color: var(--accent); }
h1 { margin-top: 12px; font-size: clamp(30px, 4.4vw, 42px); line-height: 1.12;
  letter-spacing: -.03em; font-weight: 680; }
.lede { margin-top: 16px; font-size: 18px; color: var(--ink-2); }
h2 { margin-top: 44px; font-size: 22px; letter-spacing: -.015em; line-height: 1.3; }
h3 { margin-top: 22px; font-size: 16.5px; }
p, ul, ol, pre { margin-top: 14px; }
p, li { color: var(--ink-2); }
p code, li code { padding: 1px 5px; border-radius: 5px; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink); white-space: nowrap; }
@media (max-width: 520px) { p code, li code { white-space: normal; overflow-wrap: anywhere; } }
ul, ol { padding-left: 22px; }
li { margin-top: 8px; }
pre { background: var(--code); color: var(--code-ink); border-radius: 12px; padding: 16px 18px;
  font: 13.5px/1.7 var(--mono); overflow-x: auto; }
code { font-family: var(--mono); font-size: .92em; }
.cta { display: inline-flex; margin-top: 36px; padding: 12px 18px; border-radius: 11px;
  background: var(--ink); color: var(--page); font-weight: 600; }
.cta:hover { text-decoration: none; opacity: .9; }
details { border: 1px solid var(--line); border-radius: 12px; padding: 14px 18px;
  margin-top: 12px; background: var(--surface); }
summary { cursor: pointer; font-weight: 600; color: var(--ink); }
.more { margin-top: 56px; padding-top: 28px; border-top: 1px solid var(--line); }
.more ul { list-style: none; padding: 0; display: grid; gap: 8px; }
footer { border-top: 1px solid var(--line); padding: 28px 0 40px; font-size: 13.5px;
  color: var(--muted); }
.replay { list-style: none; padding: 0; border-left: 2px solid var(--line); }
.replay li { margin: 0; padding: 7px 0 7px 16px; font-size: 15px; color: var(--ink-2); }
.who { display: inline-block; min-width: 74px; margin-right: 8px; font: 600 11.5px var(--mono);
  letter-spacing: .04em; text-transform: uppercase; color: var(--muted); }
.who.agent { color: #b45309; } .who.fiduciary { color: var(--accent); }
.who.owner { color: #4f46e5; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .who.agent { color: #f5a65b; }
  :root:not([data-theme="light"]) .who.owner { color: #a5b4fc; } }
:root[data-theme="dark"] .who.agent { color: #f5a65b; }
:root[data-theme="dark"] .who.owner { color: #a5b4fc; }
.background { font-size: 14px; color: var(--muted); }
"""
"""The guides' stylesheet: the site's colors and type, for reading."""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _url(slug: str) -> str:
    return f"{SITE}{slug}/"


def _graph(guide: Mapping[str, Any]) -> dict[str, Any]:
    url = _url(str(guide["slug"]))
    nodes: list[dict[str, Any]] = [
        {
            "@type": "TechArticle",
            "@id": url + "#article",
            "headline": guide["title"],
            "description": guide["description"],
            "url": url,
            "inLanguage": "en",
            "about": {"@id": SITE + "#app"},
            "isPartOf": {"@id": SITE + "#site"},
        },
        {
            "@type": "BreadcrumbList",
            "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Fiduciary", "item": SITE},
                {"@type": "ListItem", "position": 2, "name": guide["nav"], "item": url},
            ],
        },
    ]
    if guide["steps"]:
        nodes.append(
            {
                "@type": "HowTo",
                "name": guide["h1"],
                "step": [
                    {"@type": "HowToStep", "position": i, "name": name, "text": text}
                    for i, (name, text) in enumerate(guide["steps"], 1)
                ],
            }
        )
    if guide["faq"]:
        nodes.append(
            {
                "@type": "FAQPage",
                "mainEntity": [
                    {
                        "@type": "Question",
                        "name": question,
                        "acceptedAnswer": {"@type": "Answer", "text": answer},
                    }
                    for question, answer in guide["faq"]
                ],
            }
        )
    return {"@context": "https://schema.org", "@graph": nodes}


_COMMAND: Final = re.compile(
    r"fiduciary (?:passkey add|shadow report|solo add-principals|protect|unprotect|console|"
    r"verify-bundle|export-bundle|init|undo|infer-policy|ci start|ci finish|ci|replay)"
    r"(?: --[a-z][a-z-]*)*"
    r"|--[a-z][a-z-]+|readOnlyHint|\.mcp\.json|\.claude/settings\.local\.json|mcpServers"
    r"|\.github/(?:workflows|actions)|config\.toml|\.env\b"
)
"""What prose shows as code: a command, a flag, a file or a field."""


def _prose(text: str) -> str:
    """``text`` escaped, its commands and flags set as code."""
    out, at = [], 0
    for found in _COMMAND.finditer(text):
        out += [_e(text[at : found.start()]), f"<code>{_e(found.group(0))}</code>"]
        at = found.end()
    return "".join([*out, _e(text[at:])])


def _section(section: Mapping[str, Any]) -> str:
    out = f"<h2>{_e(section['h2'])}</h2>"
    for text in section.get("p", ()) if "code" not in section else ():
        out += f"<p>{_prose(text)}</p>"
    if "code" in section:
        out += "<pre><code>" + _e("\n".join(section["code"])) + "</code></pre>"
        out += "".join(f"<p>{_prose(text)}</p>" for text in section.get("p", ()))
    if "ul" in section:
        out += "<ul>" + "".join(f"<li>{_item(item)}</li>" for item in section["ul"]) + "</ul>"
    if "transcript" in section:
        out += (
            '<ol class="replay">'
            + "".join(
                f'<li><span class="who {_e(actor)}">{_e(actor)}</span> {_prose(text)}</li>'
                for actor, text in section["transcript"]
            )
            + "</ol>"
        )
    out += "".join(f"<p>{_prose(text)}</p>" for text in section.get("after", ()))
    if section.get("links"):
        out += (
            '<p class="background">Background:</p><ul>'
            + "".join(
                f'<li><a href="{_e(url)}" rel="noopener">{_e(label)}</a></li>'
                for label, url in section["links"]
            )
            + "</ul>"
        )
    return out


def _item(text: str) -> str:
    """A list item; one that opens with a short label ("Values. Every...") has it in bold."""
    found = re.match(r"([A-Z][A-Za-z ]{0,30})\. (.+)", text, re.S)
    if found is None:
        return _prose(text)
    return f"<b>{_e(found.group(1))}.</b> {_prose(found.group(2))}"


def page(guide: Mapping[str, Any], others: Sequence[Mapping[str, Any]] = GUIDES) -> str:
    """One guide as a whole HTML page, to be written at ``<slug>/index.html``."""
    url = _url(str(guide["slug"]))
    title = f"{guide['title']} | Fiduciary"
    nav = "".join(
        f'<a href="../{_e(str(g["slug"]))}/">{_e(str(g["nav"]))}</a>'
        for g in others
        if g["slug"] != guide["slug"]
    )
    steps = ""
    if guide["steps"]:
        steps = (
            "<h2>Step by step</h2><ol>"
            + "".join(
                f"<li><b>{_e(name)}.</b> <code>{_e(text)}</code></li>"
                for name, text in guide["steps"]
            )
            + "</ol>"
        )
    faq = ""
    if guide["faq"]:
        faq = "<h2>Questions</h2>" + "".join(
            f"<details><summary>{_e(q)}</summary><p>{_prose(a)}</p></details>"
            for q, a in guide["faq"]
        )
    related = "".join(
        f'<li><a href="../{_e(str(g["slug"]))}/">{_e(str(g["title"]))}</a></li>'
        for g in others
        if g["slug"] != guide["slug"]
    )
    label, href = guide["app"]
    graph = json.dumps(_graph(guide), separators=(",", ":")).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<meta name="description" content="{_e(str(guide["description"]))}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{_e(str(guide["title"]))}">
<meta property="og:description" content="{_e(str(guide["description"]))}">
<meta property="og:image" content="{SITE}social-card.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/svg+xml" href="../favicon.svg">
<link rel="stylesheet" href="../guides.css">
<script type="application/ld+json">{graph}</script>
</head>
<body>
<header class="top"><div class="wrap">
<a class="brand" href="../"><img src="../favicon.svg" width="20" height="20" alt="">Fiduciary</a>
<nav aria-label="Guides">{nav}<a href="{REPO}">GitHub</a></nav>
</div></header>
<main><div class="wrap">
<article>
<span class="kicker">Guide</span>
<h1>{_e(str(guide["h1"]))}</h1>
<p class="lede">{_prose(str(guide["lede"]))}</p>
{"".join(_section(s) for s in guide["sections"])}
{steps}
{faq}
<a class="cta" href="{_e(href)}">{_e(label)}</a>
</article>
<aside class="more"><h2>More guides</h2><ul>{related}</ul></aside>
</div></main>
<footer><div class="wrap">Fiduciary is open source, Apache-2.0. <a href="../">Overview</a> ·
<a href="{REPO}/blob/main/docs/QUICKSTART.md">Quickstart</a> ·
<a href="{REPO}/blob/main/docs/THREAT_MODEL.md">Threat model</a></div></footer>
</body>
</html>
"""
