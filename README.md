# ClaimPilot AI

ClaimPilot AI is a portfolio prototype for property insurance intake and restoration coordination. A customer describes damage in chat, optionally adds photographs, and confirms the facts. An operator reviews policy evidence and contractor recommendations, approves a specific proposal, then sends a simulated dispatch. Coverage guidance is advisory. Claims, policies, contractors and dispatch events in the walkthrough are synthetic.

**Walkthrough video** · _URL pending_  
**Deployment** · _URL pending_

## AI capabilities

| Capability | What the demo shows |
| --- | --- |
| Conversational intake | Structured extraction with verbatim evidence, follow-up questions for missing or conflicting facts, and customer confirmation. |
| Multimodal damage analysis | Photo observations with visible evidence, tentative interpretations and limitations. Confirmed claim facts remain under customer control. |
| Policy RAG | Upload a document, ask questions within the selected scope, and inspect cited stored clauses. The service can abstain when evidence is insufficient. |
| Agentic workflow | Inspect scoped contractor tool calls, review a versioned proposal and resume a durable LangGraph workflow through human approval and simulated dispatch. |

The stack is React 19, TypeScript, Vite, Tailwind CSS, React Router and TanStack Query in the browser. The backend uses Python 3.11, FastAPI, Pydantic, LangChain, LangGraph, SQLite, ChromaDB and CPU-based all-MiniLM-L6-v2 embeddings. Live inference uses Groq with separately configurable text and vision models. [Architecture and Mermaid diagrams](docs/ARCHITECTURE.md)

## Run the interactive demo

Use Python 3.11 with `uv` and Node.js 20.19+ on the 20.x line or 22.12+ with npm. From the repository root in PowerShell, install the locked dependencies and start the backend.

```powershell
uv sync --locked --group agents
uv run --locked --group agents python -m examples.frontend_demo --interactive
```

For **live Groq inference**, put `GROQ_API_KEY` in the backend environment or root `.env` and use this backend command instead. The first live indexing operation may download the local embedding model.

```powershell
uv run --locked --group agents python -m examples.frontend_demo --interactive --provider groq
```

In a second terminal, from the repository root, run the frontend.

```powershell
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Offline mode uses the included reports, image fixtures, lexical embeddings and named-clause answers. Use live mode for custom reports, photos and arbitrary questions. Use synthetic information in both modes. [Alternate port, manual sessions and sample photo attribution](docs/FRONTEND.md)

### Connected walkthrough

Select **Dishwasher leak** and **Try Interactive Demo**. Submit the sample messages, optionally analyze the sample photo, and confirm the claim facts. In Operator Review, bind the suggested `v1` policy, run the workflow, inspect the evidence and tool trace, then approve the proposal with a decision reason. Send the simulated dispatch and simulate acceptance. Dispatch rejection can lead to reassignment and a fresh approval. **Policy exclusion** and **No contractor available** exercise the human-review routes.

### Independent demonstrations

Use **Explore a capability** on the landing page. **Conversational AI** starts with a short report and shows clarification. **Multimodal Damage Analysis** displays the image observations and uncertainty. **Custom Knowledge Base & RAG** starts with an empty library. Import the fictional visitor policy and try its coverage, exclusion and insufficient-evidence questions. **Agentic Workflow** lets you inspect tools and practice approval or reassignment.

## Evaluation and release checks

The benchmark snapshot records 3/3 offline structured extraction cases, retrieval Hit@5 on 5/5 labeled cases, 4/4 valid named-clause citations and 3/3 abstention and scope checks. The five offline contractor scenarios recorded 13/13 valid tool executions, 2/2 eligible recommendations, 3/3 correct escalations and zero unauthorized actions. The recorded eight-case live RAG run returned 8/8 expected outcomes with 6/6 valid citations. These are small synthetic checks. [Methodology, denominators and live verification](docs/EVALUATION.md)

```powershell
uv run --locked --group agents pytest
npm.cmd --prefix frontend run typecheck
npm.cmd --prefix frontend run build
```

## Technical guides

- [Current architecture, safeguards and trade-offs](docs/ARCHITECTURE.md)
- [Deployment readiness, operating limits and public-session requirements](docs/DEPLOYMENT.md)
- API examples for [intake](docs/INTAKE_API.md), [photos](docs/PHOTO_API.md), [knowledge](docs/KNOWLEDGE_API.md) and [contractor recommendations](docs/CONTRACTOR_API.md)
- [Implementation history and future work](docs/IMPLEMENTATION_PLAN.md)
