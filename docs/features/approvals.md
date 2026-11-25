# Approvals

Some moves need someone else's yes first: a budget is approved by a controller, never by the
planner who submits it. A module says which moves of its state flows need approval; pressing
such a move's button asks for it instead of moving.

## Declaring

```python
Transition(
    "submit", "Submit for approval", "draft", "approved",
    approval=Approval(
        steps=(Step("budget.controller"), Step("finance.director", "total", "50000")),
        remind_after=3, escalate_after=7, escalate_to=None,
    ),
)
```

| Part | Meaning |
|---|---|
| `steps` | In order; each needs a yes from someone holding its role |
| `Step(role, field, above)` | With `field` and `above`: only when the record's number field is above that |
| `remind_after` | Days a step may wait before its approvers are reminded (once) |
| `escalate_after` | Days before it's also given to `escalate_to` (a role), or to the administrators |

Each such move becomes a record of `sys.approval_rule`. Administrators change its steps, days and
escalation in the app (Automation → **Approval rules**); a rule they changed is kept as it is
when the module is updated.

## Asking and deciding

- Pressing the move's button (`POST …/action/<module>.move_<name>`) asks for approval. The
  steps that apply to the record are kept with the request; if none applies (all below their
  amounts), the move is made at once.
- A step's approvers are everyone holding its role, **never the person who asked**, and the
  **deputies** of those away (below). If nobody holds it, the administrators decide; if nobody
  but the person asking could, asking is refused.
- One yes per step moves it on; the last one makes the move, as the system (the approver needn't
  be allowed to change the record otherwise). One no ends it: a no needs a reason, and the record
  stays where it was.
- **Who asked** can withdraw it while it waits. Approvers are notified when it reaches them
  (`sys.approval_asked`), and who asked when it's decided (`sys.approval_decided`, with the
  reason of a no).
- **While it waits**, the record doesn't change (but by the system), and the move can't be made
  around the approval (`needs_approval`). A module can ask whether a record waits
  (`Client.pending_approval`), to hold its lines too.
- The daily job **Remind and escalate waiting approvals** reminds a step's approvers after
  `remind_after` days and gives it to the escalation role after `escalate_after` days.

## Deputies

People name who stands in for them while they're away (My work → **My deputies**: a deputy, from,
until). A step reaching someone away goes to their deputy too; the decision says for whom.

## In the app

- The record's form shows the approval waiting (or the last one rejected) above its sections:
  each step with its role, who decided and why, who it waits for, and **Approve**, **Reject**
  (saying why) and **Withdraw** for those who may.
- **My approvals** (My work) lists what waits for the person, oldest first, to approve or reject
  right there; the home widget **Waiting for my approval** counts it.
- Administrators see every approval (Automation → **Approvals**) and the rules.

## API

| Route | What |
|---|---|
| `GET /api/v1/approvals/mine` | What waits for the caller's approval |
| `GET /api/v1/<module>/<table>/<id>/approvals` | A record's approvals, newest first, with `may_decide` and `may_withdraw` (for those who may read the record) |
| `POST /api/v1/approvals/<id>/approve` | A yes (`note` optional) |
| `POST /api/v1/approvals/<id>/reject` | A no (`note` required) |
| `POST /api/v1/approvals/<id>/withdraw` | Who asked takes it back |

Modules: `Client.approvals(table, id)`, `Client.decide(approval, "approve" | "reject" |
"withdraw", note=)` and `Client.pending_approval(table, id)`.
