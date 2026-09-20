# Agent prompt template

One template for every step. Replace `<N>`, and add the handoff line only when there
is something true of *this* run that is not already in the docs (a step landing out
of order, a spec amendment a previous agent made, a deliberate deviation).

Everything else lives in `docs/SPEC.md` (design truth) and the "Ground rules for
every step" section of `docs/PLAN.md` (process, environment, definition of done).
**Do not paste those into a prompt.** A prompt that restates them creates a second
source of truth that drifts from the first.

---

```
Implement Step <N> of the job application tracker.

Working directory: /Users/caolan/Developer/claude/application-tracker

Read docs/PLAN.md — the "Ground rules for every step" section first, then Step <N>.
Those two sections are your complete instructions: read order, environment, scope,
definition of done and how to report back. Follow them exactly.

<handoff line, if any>
```

---

## Where things live

| Kind of information | Home | Example |
|---|---|---|
| What correct means | `SPEC.md` | the data model, the JSON contracts, D14's anti-fabrication rules |
| Why it is that way | `SPEC.md` §8 | why SQLite over CSV, why no LLM API call |
| What to build, in order | `PLAN.md` steps | "Step 10 — Salary" |
| Traps in one step | that step's **Pitfalls** | `PRAGMA foreign_keys` is per-connection |
| True of every step | `PLAN.md` ground rules | the venv, the commit convention |
| True of this run only | the prompt's handoff line | "Step 3 is not merged yet; stub it" |

If you catch yourself writing something into a prompt twice, it belongs in one of the
first five rows instead.
