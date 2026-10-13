# Operations, backup and rollback

## Shared work and explicit critical holds

Jobs share the host by default. Native desktop input holds a short per-host lock.
This lets separate background work continue while one agent uses a desktop.
It does not resolve app-level conflicts: two scripts can still edit the same file.
Use separate directories/resources and agree on scope before dispatch.

For a critical phase, explicitly call `work_claim` with a new public action ID and
agent ID. Claim refuses an owner pause, an active exclusive reservation, a recorded
running job or an uncertain action. The returned private token is required on
new jobs, input and staging during the reservation. Read/cancel operations remain
available for reconciliation. Renew with a new action ID before expiration;
an expired token cannot revive the old claim. Release when done. TTL is 30..900
seconds, default 300.

An exclusive reservation is a logical host hold, not a kernel lock, permission
grant, transaction or cancellation mechanism. Expiring/releasing it does not stop
a background job. It cannot override owner pause. Agents with arbitrary shell
access under the same account are trusted and can bypass local coordination.

## Human takeover

Use Take control in the private Studio, or run privately on the host:

```sh
diy-stack owner --config /absolute/path/to/private/worker.json pause
```

Pause blocks new jobs, staging and desktop input, removes the owner input grant,
and is checked during bounded native actions. It does not roll back prior input
or stop an already-running background job. Cancel that job by its exact ID if
needed, then inspect partial effects.

The baseline has no physical-human input detector. Use a dedicated X11 session
and pause before a human uses it. Together is disabled. Watch and VNC reconnect
never clear the pause. After the human is done, the owner explicitly runs:

```sh
diy-stack owner --config /absolute/path/to/private/worker.json resume
diy-stack owner --config /absolute/path/to/private/worker.json allow-input --ttl 300
```

Resume clears pause; it does not grant native input. An input grant expires in
30..900 seconds and binds the configured session generation. Refresh the session
ID after a desktop restart. Observe focus and geometry again after a resize,
reconnect, modal or app switch.

## Receipts and unknown results

Every job and desktop mutation needs a unique lowercase public ID. Reusing the
same ID with the same request returns the stored receipt; different contents are
rejected. No mutation is automatically replayed after SSH timeout or disconnect.

`input_sent` reports dispatch, not a successful save, submission or app outcome.
`completed` reports a clean job exit and acknowledged group cleanup, not the goal's
real effect. Read the receipt and verify the target file, app state or command
result independently.

If a receipt says `unknown_outcome`, inspect the exact action, private logs and
service group before issuing a new mutation. A disconnected client is not proof
that the host stopped. Native actions have an eight-second budget; cancellation
can leave partial effects. The coordinator sends the exact cancel marker while
the worker is still alive, then gives it bounded time to stop its input process
group and record a receipt. Four normal calls and four separate cancellation
calls are the maximum concurrent channels. A forced kill or broken connection
can still require owner inspection. User jobs have bounded runtimes and output; systemd
group cleanup is checked when their terminal receipt is read.
Launches, cancellations and terminal group cleanup persist uncertainty before
asking the service manager to mutate state. An interrupted worker leaves that
fence in place until the owner inspects the exact unit and its partial effects.

Reading a terminal receipt again may retry cleanup of its exact `diy-stack-` job
unit. It never launches the script again. A failed unit can retain an unavailable
task count after its processes stop. Cleanup requires zero main and control PIDs,
a stopped unit, and either an explicitly empty control group or proof that the
exact group is unpopulated. A positive or malformed task count refuses cleanup.
For cgroup v2, the worker reads the exact group's `cgroup.events`; it never clears
a populated or unverified group. Where that interface is absent, a nonempty group
also requires an explicit zero task count.

After an acknowledged exact stop and empty-group proof, the worker may call
`reset-failed` for that one failed unit, then verify the result again. This clears
the manager's retained failure metadata while the receipt preserves the original
exit code and execution status. A cleanup timeout or failed proof leaves
`unknown_outcome`. A later cleanup-only retry can acknowledge process cleanup;
it cannot resolve the action's effects or clear that uncertainty. Each external
retry first persists fresh false acknowledgements, so worker loss cannot reuse
an earlier attempt's success.

After manual inspection and private backup, the owner may mark an uncertain action
as reconciled. This records the owner's confirmation; it does not run or undo it:

```sh
diy-stack owner --config /absolute/path/to/private/worker.json reconcile --action-id EXACT_ID --confirmed-effects
```

No reconcile command should run until the owner has verified the affected work
and any remaining processes. Keep old IDs retired.

An `unknown_outcome` blocks new mutations until owner reconciliation. Read and
cancel tools remain available. An orphaned `dispatching` native record also blocks
further input. If a worker was killed before it could finalize that record, inspect
and stop its actual native processes, then preserve a private backup and repair
that exact record manually. No automatic state reset or permission recovery is
performed.

## Private backup

Before important changes, pause agents and stop owned active jobs. Back up the
affected application data/config to an owner-only location outside this repo.
Include modes, hashes, timestamps and the original paths in your private notes.
Do not publish that backup or native account/profile data.

The private worker state contains scripts, output, receipts and reservation
tokens. Back it up only after no writer is active. Do not sync a live SQLite
database or locks between hosts. Vault runtime staging/session material should
be locked and removed with `vault-lock`, not copied into a backup or bundle.

## Stop and roll back

Pause first. Read/cancel the exact owned job IDs and verify their service groups
are stopped. Close Studio and the explicit SSH/websockify/VNC processes you
started. Do not stop unrelated desktop, SSH or owner services.

Installation creates no service. To return to the prior setup, restore backed-up
configs/application data, unregister this MCP command from your agent and use
the previous environment. Keep private receipts until outcomes are reconciled.
Remove only this project's files after the owner approves cleanup; the package
has no automatic destructive uninstall or history deletion command.

## Repository hygiene

Only commit example configs and public fixtures. Real addresses, SSH logins,
keys, vault sessions, personal/work context, cookies, profiles, screenshots,
clipboard text, output logs, databases and backups stay outside the checkout.
The `.gitignore` is convenience, not proof of a clean tree. Run the public-tree
scanner and inspect the exact staged Git diff before publishing anything.
