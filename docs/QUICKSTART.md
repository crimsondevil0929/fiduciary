# Put it in front of your agent in 30 minutes

## First, one tool in shadow mode (60 seconds)

The quickest way to see what the stack would do is to wrap one tool you already have and let
it watch. In shadow mode every call goes through as it does today, and the stack records
which calls it would have refused, and why. Nothing is blocked until you say so.

```bash
pip install interlock-fiduciary
```

A project with a SQLite database of three open tickets:

<!-- quickstart: run -->
```bash
mkdir helpdesk && cd helpdesk
python3 -c "
import sqlite3
conn = sqlite3.connect('app.db')
conn.executescript('''CREATE TABLE tickets (id INTEGER PRIMARY KEY, status TEXT NOT NULL);
INSERT INTO tickets VALUES (1, 'open'), (2, 'open'), (3, 'open');''')
conn.commit()"
fiduciary init --solo --shadow --substrate sqlite --database app.db
```

`--solo` writes a deployment of one principal, you, into `.fiduciary/` (its `.gitignore`
keeps it all out of version control). `--shadow` issues its mandate in shadow mode. The
starter mandate lets one call change one row.

Two tools, written as you would write them, with a handle in place of a connection. Save
them as `tools.py`:

<!-- quickstart: file tools.py -->
```python
import fiduciary

gov = fiduciary.open()


@gov.tool
def close_ticket(db: fiduciary.Sql, ticket_id: int) -> str:
    """Close one ticket."""
    db.execute("UPDATE tickets SET status = 'closed' WHERE id = :id", {"id": ticket_id})
    return f"ticket {ticket_id} closed"


@gov.tool
def close_all(db: fiduciary.Sql) -> str:
    """Close every open ticket."""
    db.execute("UPDATE tickets SET status = 'closed' WHERE status = 'open'")
    return "all closed"


print(close_ticket(ticket_id=1))
print(close_all())
```

<!-- quickstart: run -->
```bash
python3 tools.py
fiduciary shadow report
```

Both calls went through, and the report names the one the mandate would have refused:

```
  2 plans staged: 2 committed, 0 refused, 0 put back.
  1 of them would have been refused if the mandate were enforced:
    blast_radius: 1 plan, from close_all (1)
```

When the report shows only what you meant to refuse, enforce the same bounds. From then on
`close_all` raises `fiduciary.Refused` with nothing changed, and its text, written for the
agent, says why:

<!-- quickstart: run -->
```bash
fiduciary solo enforce
cd ..
```

Each call runs in a stage: the function's writes through its handle land there, and when it
returns they are measured and checked as one plan. Only what goes through the handle is
staged. A tool that opens its own connection writes outside the stage, where nothing
measures it. `fiduciary.adapters` hands the same tools to MCP, the Claude Agent SDK, the
OpenAI Agents SDK and LangGraph, and `fiduciary solo add-principals` grows the deployment
to three principals, two of whom must agree to grant more.

The rest of this guide is the full deployment: three principals, an agent over MCP, an
exception two of three approve, and the audit bundle.

This guide takes you from `pip install` to an agent whose writes are measured before they
commit, an exception two of three people approve, and an audit bundle anyone can verify
offline. It uses a small SQLite database so every step runs on a laptop. The same steps
work on PostgreSQL, a Git repository, a directory of files, a Kubernetes namespace, a
Terraform configuration, a FHIR server, or a JSON REST API: see [step 2](#2-point-it-at-your-system-5-minutes).

Every command block marked to run below is run, in order, by this repository's tests
(`tests/test_quickstart.py`), so the guide cannot drift from the code.

You need Python 3.11 or later. Nothing here calls a model or needs an API key.

## 1. Install it, and see it work (2 minutes)

```bash
pip install 'interlock-fiduciary[demo]'
```

<!-- quickstart: run -->
```bash
fiduciary --version
```

To see the whole stack before you set anything up, run the demo. It plays eight agent
workflows (recorded `claude-opus-5-5` sessions where it has them, scripted calls elsewhere) and
opens the console in your browser:

```bash
fiduciary demo --replay --out demo --serve --open
```

The same console is at <https://crimsondevil0929.github.io/fiduciary/>.

## 2. Point it at your system (5 minutes)

A database to govern: customers, orders, and an audit table that a trigger writes whenever
an order's status changes. That trigger is the kind of consequence a tool's description
never mentions.

<!-- quickstart: run -->
```bash
python3 - <<'EOF'
import sqlite3
conn = sqlite3.connect("shop.db")
conn.executescript("""
CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, email TEXT);
CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, status TEXT NOT NULL);
CREATE TABLE audit (at TEXT, note TEXT);
CREATE TRIGGER orders_audit AFTER UPDATE OF status ON orders BEGIN
  INSERT INTO audit VALUES (datetime('now'), 'order ' || NEW.id || ' is ' || NEW.status);
END;
INSERT INTO customers VALUES (1, 'Ada', 'ada@example.com'), (2, 'Grace', 'grace@example.com');
INSERT INTO orders VALUES (1, 1, 'open');
""")
conn.commit()
EOF
fiduciary init --out desk --substrate sqlite --database shop.db
```

`init` reads the database and writes a deployment into `desk/`:

- starter tools (`insert_<table>`, `update_<table>`), each a signed binding;
- a starter mandate: inserts and updates only, one row a plan, at most 120 plans an hour,
  no deletes;
- a charter with three principals (`approver-1` to `approver-3`) and their keys, any two of
  whom decide an exception;
- the configuration (`fiduciary.toml`, `interlock.toml`) and a `README.md` with every
  command for this deployment.

It also says what it found that the starter mandate will refuse. Here it names the trigger:
every `update_orders` call writes an audit row too, so it changes two rows, and the mandate
allows one. Step 5 deals with that.

For another system, only the `init` line changes:

```bash
fiduciary init --out desk --substrate postgres --database 'postgresql://stage_role:...@host/db'
fiduciary init --out desk --substrate git --repository /path/to/repo.git
fiduciary init --out desk --substrate filesystem --directory /srv/checkout-config
fiduciary init --out desk --substrate kubernetes --server https://... --token-file token --namespace prod
fiduciary init --out desk --substrate terraform --directory infra/
fiduciary init --out desk --substrate fhir --server https://fhir.example/fhir --type MedicationRequest
fiduciary init --out desk --substrate rest --server https://desk.example/api --resource tickets=/tickets
fiduciary init --out desk --mcp -- <the command that starts your MCP server>
```

On PostgreSQL, run the `grants.sql` it writes as the schema's owner first. With `--mcp`,
`init` reads the server's tools and governs each by its annotations: reads pass through,
writes go through the outbox, and destructive ones wait for two of three principals.

## 3. Start it, and connect your agent (3 minutes)

Start the writer. It holds the deployment's ledger, serves the agent's tools on
`127.0.0.1:8765`, records approvals, forms warrants and carries them out. Leave it running in
a second terminal:

<!-- quickstart: run background -->
```bash
fiduciary --config desk/fiduciary.toml serve
```

The agent gets the governed tools over MCP, with the bearer token `init` wrote to
`desk/keys/agent.token`. It never gets a database credential. With Claude Code:

```bash
claude mcp add --transport http shop http://127.0.0.1:8765/mcp \
  --header "Authorization: Bearer $(cat desk/keys/agent.token)"
```

Any MCP client that speaks Streamable HTTP takes the same URL and header. To follow this
guide without an agent, use a small client that calls the tools the way an agent does, in one
session:

<!-- quickstart: run -->
```bash
cat > call.py <<'EOF'
"""python call.py TOOL JSON [TOOL JSON ...]: call desk's tools over MCP, in one session."""
import json, sys, urllib.request

token = open("desk/keys/agent.token").read().strip()
session = None
def ask(n, method, params):
    global session
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if session:
        headers["Mcp-Session-Id"] = session
    body = json.dumps({"jsonrpc": "2.0", "id": n, "method": method, "params": params}).encode()
    request = urllib.request.Request("http://127.0.0.1:8765/mcp", data=body, headers=headers)
    with urllib.request.urlopen(request) as answer:
        session = answer.headers.get("Mcp-Session-Id") or session
        return json.loads(answer.read())["result"]
try:
    ask(0, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    for n, (tool, arguments) in enumerate(zip(sys.argv[1::2], sys.argv[2::2]), 1):
        result = ask(n, "tools/call", {"name": tool, "arguments": json.loads(arguments)})
        print(f"{tool}: {'refused' if result.get('isError') else 'ok'}")
        print(result["content"][0]["text"], end="\n\n")
finally:  # end the session: an agent holds at most four open at once
    if session:
        end = urllib.request.Request("http://127.0.0.1:8765/mcp", method="DELETE", headers={
            "Authorization": f"Bearer {token}", "Mcp-Session-Id": session})
        urllib.request.urlopen(end).close()
EOF
```

One session at a time can also run over stdio, with no writer:
`fiduciary --config desk/fiduciary.toml mcp serve --mandate desk-agent`. A SQLite ledger has
one governor process at a time, so stdio sessions and the writer take turns.

## 4. The first governed calls (3 minutes)

<!-- quickstart: run -->
```bash
python3 call.py \
  update_customers '{"id": 1, "name": null, "email": "ada@new.example"}' \
  update_orders '{"id": 1, "customer_id": null, "status": "shipped"}'
```

```
update_customers: ok
Committed: update_customers ran as plan plan-....

update_orders: refused
The plan was refused and rolled back; nothing changed.
- blast_radius: the plan changed 2-9 rows; the limit is 1. ...
```

The email change was staged, measured (one row), checked against the mandate and
committed. The status change was staged too, and the measurement found the trigger's audit
row: two rows, over the limit, so it was rolled back before anything changed. The agent
was told why in words it can act on. A gateway that checks only the tool call would have let
both through.

## 5. An exception, decided by two of three (10 minutes)

Allowing two rows a plan is a change to the mandate, and the mandate says an amendment
takes two of the three principals. The agent can ask for one. It can never write one.

A principal drafts the next mandate version, allowing two rows a plan, signs it with their
key and proposes it:

<!-- quickstart: run -->
```bash
python3 - <<'EOF'
import json
from datetime import UTC, datetime
from pathlib import Path
from fiduciary import fidu1
from fiduciary.documents import Mandate

current = Mandate.loads(Path("desk/mandate.json").read_bytes())
draft = {**current.body(), "version": current.version + 1, "prev": current.digest,
         "issued_at": fidu1.format_instant(datetime.now(UTC))}
draft["data"]["max_rows"] = 2
Path("next.draft.json").write_text(Mandate.from_json(draft, signed=False).dumps())
EOF
fiduciary sign next.draft.json --key desk/keys/approver-1.key --out next.json
NEXT=$(fiduciary --config desk/fiduciary.toml mandate propose next.json | tail -1)
echo "proposed version 2: $NEXT"
```

The agent asks for it, with its reason. Its reason is shown to the principals as an
unverified claim, apart from the evidence:

<!-- quickstart: run -->
```bash
python3 call.py fiduciary_request "{\"kind\": \"amend\", \"mandate\": \"$NEXT\", \"statement\": \"Each status change writes one audit row; please allow two rows a plan.\"}"
fiduciary --config desk/fiduciary.toml tickets list
```

Now two principals decide. In the browser:

```bash
fiduciary console --dir desk --accept-approvals --open
```

Open **Approvals**, then the ticket. It shows what would change (`data.max_rows: is 2, above
the parent's 1`), the options and their quorums, and the agent's statement apart. Under
**Sign a vote**, pick `approver-1`, the option `amend`, and the key file
`desk/keys/approver-1.key`, then sign. The key is read in the page and never sent. Do the
same as `approver-2`.

Or from the command line, each principal on their own machine, from the ticket's signed
bytes:

<!-- quickstart: run -->
```bash
TICKET=$(fiduciary --config desk/fiduciary.toml tickets list | awk '{print $1}')
fiduciary --config desk/fiduciary.toml tickets show "$TICKET" --out ticket.json
for who in approver-1 approver-2; do
  fiduciary --config desk/fiduciary.toml approve ticket.json --principal "$who" \
    --key "desk/keys/$who.key" --resolution amend --yes --submit
done
until fiduciary --config desk/fiduciary.toml tickets list --all | grep warranted >/dev/null; do sleep 1; done
fiduciary --config desk/fiduciary.toml tickets list --all
```

At two approvals the writer, still running from step 3, forms a warrant, a signed record of who approved what, and
issues version 2. The same call now commits, measured at two rows:

<!-- quickstart: run -->
```bash
python3 call.py update_orders '{"id": 1, "customer_id": null, "status": "shipped"}'
```

## 6. Audit it offline (3 minutes)

Export the lineage's bundle, and verify it with no database and no network. Exporting signs a
new checkpoint of the ledger and of the receipt log, and each has one writer at a time, so
stop the writer first (Ctrl-C in its terminal). `desk/README.md` has both commands with this
deployment's mandate and charter digest written in:

<!-- quickstart: stop -->
<!-- quickstart: run -->
```bash
MANDATE=$(python3 -c "import json; print(json.load(open('desk/mandate.json'))['mandate_id'])")
fiduciary --config desk/fiduciary.toml export-bundle --mandate "$MANDATE" \
  --ledger-key desk/keys/ledger-checkpoints.key --out desk.fidb
fiduciary verify-bundle desk.fidb \
  --charter "$(cat desk/charter.digest)" --keys desk/pinned-keys.json
```

The verifier checks V1 to V13: the container, the charter you pinned, the delegation, the
compiled policy, every receipt, the ledger, that every act traces to a mandate or a warrant,
that the warrant had its quorum, the ordering in time, and that no plan made after the agent
read untrusted input skipped the approval its mandate asked for. It ends `PASS`. Drop
`desk.fidb` on the console's **Verify a bundle** page to check it in a browser too.

`--charter` and `--keys` are what make the check mean something. A bundle carries its own
`keys.json`, and a bundle checked only against itself can vouch for itself. `init` wrote the
deployment's public keys to `desk/pinned-keys.json` and the charter's digest to
`desk/charter.digest`. Neither file holds a secret. Hand both to your auditor once, by a
channel the bundles do not travel on, and they check every later bundle against them.

A system that keeps its own log of writes can be matched against the bundle alone. A
SQLite file keeps none, so see it on the demo from step 1, against Stripe's event log:

```bash
cd demo && fiduciary reconcile-actions after.fidb audit/stripe-events.json \
  --charter $(cat charter.digest) --keys pinned-keys.json \
  --format stripe-events --actor api --substrate stripe
```

It explains each refund by a delivery receipt in the bundle and names the one made in
Stripe's dashboard, around the agent.

## 7. Before you rely on it

- **Move the private keys.** `init` wrote every private key into `desk/keys/` so this guide
  could run on one machine: the three principals', the service's, the ledger's, and the
  agent's bearer token beside them. Whoever holds two principals' keys can sign a warrant.
  Give each principal their key (or move it to a KMS) and delete it here. Keep
  `agent.token` with the agent alone, and the service and ledger keys with the service.
- **Read [what it does and does not protect against](THREAT_MODEL.md).**
- **Tighten the starter tools** to what your agent needs. Edit them under `desk/tools/`, sign
  them with `desk/keys/developer.key`, and issue a mandate version that pins them.
- **Keep the pins apart from the bundles.** `desk/pinned-keys.json` and
  `desk/charter.digest` are public, but an auditor who receives them with a bundle can be
  handed a forged set. Give them over once, out of band. A new mandate version keeps them;
  a new charter or a rotated key changes them.
