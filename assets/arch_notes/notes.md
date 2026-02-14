# Agent Coordinator

```
The CortexOS agent team is the 7 self-registering agents working together. But right now the coordination is mostly **sequential and API-driven** — not a true multi-agent protocol. Let me be honest about what exists versus what would make it a real coordinated team.

**What exists today:**

The ingest pipeline is sequential — video comes in, Whisper transcribes, Opus plans, CLIP extracts, Weaviate indexes. Each step calls the next. The fact-verifier reads the output of the planner. The synthesis agent reads from Weaviate. They share data through the filesystem (`/data/out/{video_id}/`) and MongoDB, not through direct agent-to-agent messaging.

Opus 4.6 acts as the **planner** but not as a **coordinator**. It decides the strategy for a single video, then hands off. It doesn't watch the other agents, reassign work, or resolve conflicts between them.

**What's missing for true coordination:**

A real agent team needs an orchestration layer where Opus 4.6 acts as a **team lead** — it assigns tasks, monitors progress, re-plans when something fails, and synthesizes results from multiple agents working in parallel.

For your ETH example, the ideal flow would be:

```
Opus 4.6 (team lead) receives: 10 videos + ETH exchange data
    ↓
Opus reasons: "3 videos are price predictions, 4 are on-chain analysis,
               3 are macro commentary. The exchange data is relevant to
               the on-chain group. I'll parallelize."
    ↓
Assigns simultaneously:
    → Video Ingest Agent: process all 10 (parallel)
    → Fact Verifier: stand by for claims
    → Data Agent: parse ETH exchange CSV, compute metrics
    ↓
Opus monitors: "8/10 videos done. 2 failing on download. Skip them."
    ↓
Assigns next wave:
    → Fact Verifier: verify on-chain claims against ETH data
    → Fact Verifier: verify price targets against current market
    → Synthesis Agent: find contradictions across speakers
    ↓
Opus receives all results, reasons about conflicts:
    "The fact-verifier says Analyst A's flow claim is wrong,
     but the synthesis agent found Analyst B made the same claim
     and the data supports B's version. These need reconciliation."
    ↓
Final output: coordinated report with resolved conflicts
```

That doesn't exist yet. What you'd need to build is an **agent_coordinator.py** — a new agent where Opus 4.6 runs in a loop, receives status updates from all agents, and makes real-time decisions about what to do next. The key primitives would be a shared task queue in MongoDB, agent status heartbeats, and Opus re-planning when agents fail or produce unexpected results.

The difference between what you have and a true agent team is the difference between a relay race (each runner waits for the baton) and a basketball team (everyone moves simultaneously, the point guard reads the situation and calls plays in real time). CortexOS is currently the relay race. Opus as team coordinator makes it the basketball team.

Want me to build the coordinator?

```
