# Manual smoke test: `beszel` + `dbhub`

Verify the two services added in v0.4.11 deliver their end-user outcome.
Walk through both in ~5 minutes.

---

## Beszel — monitor this host

**Outcome:** open the hub URL and the local Harbor host is already in the dashboard with live metrics. No signup, no Add-System click, no key paste.

```bash
harbor up beszel --open
```

The browser opens already signed in (via beszel's `AUTO_LOGIN`). **Pass:** within ~10 seconds you see a `harbor-host` row with live values for CPU %, Memory, Disk, Load, and Temperature. Click into the row — graphs populate within a minute.

If you ever need the admin credentials (e.g. to log in from another browser):

```bash
harbor config get beszel.user.email
harbor config get beszel.user.password
```

```bash
harbor down beszel
```

---

## DBHub — query a database from Open WebUI

**Outcome:** Open WebUI talks to a SQL database through DBHub's MCP tools — no manual configuration.

```bash
harbor up webui dbhub --open
```

In Open WebUI, sign in (or create an admin), pick a tool-calling model, and ask:

> *"List 3 employees from the database with their salaries."*

**Pass:** the model invokes the `execute_sql` tool and returns three rows from the demo `employees` table.

To point at a real database, set a DSN and restart:

```bash
harbor config set dbhub.dsn "postgres://user:pw@host:5432/dbname?sslmode=disable"
harbor restart dbhub
```

The same chat now answers questions about your data.

```bash
harbor down dbhub webui
```
