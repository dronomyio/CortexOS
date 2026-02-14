# CortexOS — Project Description

**CortexOS** is an autonomous video intelligence platform built for the Claude Code Virtual Hackathon. It transforms unstructured video content into verified, searchable, monetizable intelligence — with **Opus 4.6** reasoning at the core of every decision.

Unlike conventional video pipelines that apply identical processing to every input, CortexOS places Opus 4.6 as a **dynamic planner** that reads each transcript and reasons about the optimal analysis strategy before any work begins. For a video dense with price predictions, Opus selects an investigative verification strategy. For market commentary, it chooses comparative synthesis. For breaking news, it prioritizes factual cross-referencing. This planning layer means the system gets smarter about *how* it works, not just *what* it processes.

The platform comprises **seven self-registering agents** discovered automatically at startup — fact-verification (with live web checking via Parallel.ai), Opus-planned synthesis, semantic search across visual and transcript embeddings in Weaviate, video QA, observability tracing through Opik, and x402 payment processing.

CortexOS operates as both an **x402 micropayment server and client** on Arc Network. External AI agents pay $0.01–$0.25 per query in USDC via Circle Wallets; CortexOS pays upstream data providers for verification. The margin is built-in revenue — no subscriptions, no API key management, just protocol-native commerce.

The Claude Code integration includes three skills (`/ingest-video`, `/find-contradictions`, `/deploy`), two auto-firing hooks, and **17 MCP tools** exposing the complete platform to any MCP-compatible client. Everything runs in Docker with MongoDB persistence and a real-time React dashboard.

**Video in. Verified facts out. Payments settled. Opus 4.6 reasoning at every layer.**
