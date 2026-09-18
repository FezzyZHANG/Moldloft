# Moldloft agent entry

Read [AGENTS.md](AGENTS.md) before editing. It is the single development contract for this
repository: purpose, communication, data ownership, command safety, verification and completion.
Use its reading route and the [design task map](DESIGN.md#navigation) for the current task.

Follow the [startup sanity and self-bootstrap contract](AGENTS.md#startup-sanity-and-self-bootstrap)
when a `<moldloft-sanity>` block is present or local lifecycle wiring has not been installed.
The offline setup and checks live in this repository and require only Python's standard library.
This pointer introduces no separate policy or dependency on another checkout.
