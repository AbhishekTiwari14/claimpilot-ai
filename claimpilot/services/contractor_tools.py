from hashlib import sha256
from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from claimpilot.schemas.contractors import (
    Arrival, Availability, CandidateArgs, Contractor, DispatchRequirements, NoArgs, Performance,
    ProposalDraft, RequirementSource, SearchArgs, SearchResults, Trade, Urgency,
)
from claimpilot.schemas.knowledge import Operator, SearchRequest
from claimpilot.services.knowledge_index import KnowledgeIndex
from claimpilot.storage.contractors import ContractorStore, digest, eligible
from claimpilot.storage.intake import IntakeError

HANDBOOK = Path(__file__).resolve().parents[2] / "data" / "knowledge" / "restoration-operations.md"


class ContractorTools:
    def __init__(self, store: ContractorStore, knowledge: KnowledgeIndex, actor: Operator,
                 zip_code: str, trade: Trade, urgency: Urgency, *,
                 excluded: frozenset[str] = frozenset(), allow_sla_defaults: bool = False) -> None:
        self.store, self.knowledge, self.actor = store, knowledge, actor
        self.zip_code, self.trade, self.urgency = zip_code, trade, urgency
        self.excluded, self.allow_sla_defaults = excluded, allow_sla_defaults
        self.discovered: set[str] = set()
        self.observations: dict[tuple[str, str], BaseModel] = {}
        self.requirements: DispatchRequirements | None = None

    def definitions(self) -> list[StructuredTool]:
        definitions = (
            ("search_contractors", SearchArgs, "Find eligible IDs for the bound claim."),
            ("check_availability", CandidateArgs, "Check current capacity of a discovered ID."),
            ("get_estimated_arrival", CandidateArgs, "Read arrival minutes for a discovered ID."),
            ("get_contractor_performance", CandidateArgs, "Read rating, history and simulated cost."),
            ("get_dispatch_requirements", NoArgs, "Read verified SLA and approval requirements."),
            ("propose_dispatch", CandidateArgs, "Draft a recommendation after all checks. No dispatch."),
        )
        return [
            StructuredTool.from_function(getattr(self, name), name=name, description=description, args_schema=schema)
            for name, schema, description in definitions
        ]

    def observe(self, name: str, result: BaseModel) -> None:
        if isinstance(result, SearchResults):
            self.discovered.update(result.candidate_ids)
        elif isinstance(result, DispatchRequirements):
            self.requirements = result
        elif isinstance(result, (Availability, Arrival, Performance)):
            self.observations[(name, result.contractor_id)] = result

    def search_contractors(self, limit: int = 3) -> SearchResults:
        now = self.store.now()
        candidates = [
            item for item in self.store.contractors(self.actor)
            if item.id not in self.excluded and eligible(item, self.zip_code, self.trade, now)
        ]
        candidates.sort(key=lambda item: (item.arrival_minutes, -item.completion_rate, -item.rating, item.id))
        return SearchResults(candidate_ids=[item.id for item in candidates[:limit]])

    def _discovered(self, contractor_id: str):
        if contractor_id not in self.discovered:
            raise IntakeError(422, "not_discovered", "Use a contractor returned by the search tool.")
        return self.store.contractor(self.actor, contractor_id)

    def check_availability(self, contractor_id: str) -> Availability:
        return self._availability(self._discovered(contractor_id))

    def _availability(self, item: Contractor) -> Availability:
        return Availability(contractor_id=item.id, eligible=eligible(
            item, self.zip_code, self.trade, self.store.now()), capacity=item.capacity)

    def get_estimated_arrival(self, contractor_id: str) -> Arrival:
        item = self._discovered(contractor_id)
        return Arrival(contractor_id=item.id, minutes=item.arrival_minutes)

    def get_contractor_performance(self, contractor_id: str) -> Performance:
        return self._performance(self._discovered(contractor_id))

    @staticmethod
    def _performance(item: Contractor) -> Performance:
        return Performance(contractor_id=item.id, rating=item.rating, acceptance_rate=item.acceptance_rate,
                           completion_rate=item.completion_rate, simulated_cost_usd=item.estimated_cost_usd)

    def get_dispatch_requirements(self) -> DispatchRequirements:
        chunks = self.knowledge.search(self.actor.tenant_id, SearchRequest(
            document_type="operations", query="contractor dispatch authorized human approval SLA urgent acknowledgement arrival escalation",
            top_k=12,
        ))
        approved = sha256(HANDBOOK.read_bytes()).hexdigest()
        sections = {item.section: item for item in chunks if item.content_hash == approved}
        required = ("O1 Contractor dispatch", "O2 SLA and escalation")
        if any(section not in sections for section in required):
            if self.allow_sla_defaults:
                return DispatchRequirements(
                    acknowledgement_limit_minutes=60 if self.urgency == "high" else 240,
                    arrival_limit_minutes=240 if self.urgency == "high" else 1440,
                    sources=[], provenance="synthetic_default",
                )
            raise IntakeError(422, "requirements_unavailable", "Index the approved synthetic operations handbook.")
        approval, sla = (sections[section].text for section in required)
        if ("authorized human approval" not in approval
            or "routine assignment within four hours" not in sla
            or "acknowledgement within one hour and arrival within four hours" not in sla):
            raise IntakeError(422, "requirements_unavailable", "The approved requirements are incomplete.")
        urgent = self.urgency == "high"
        return DispatchRequirements(
            acknowledgement_limit_minutes=60 if urgent else 240,
            arrival_limit_minutes=240 if urgent else None,
            sources=[RequirementSource(
                chunk_id=sections[name].chunk_id, filename=sections[name].filename,
                section=name, excerpt=sections[name].text,
            ) for name in required],
        )

    def propose_dispatch(self, contractor_id: str) -> ProposalDraft:
        item = self._discovered(contractor_id)
        requirements = self.requirements
        if requirements is None:
            raise IntakeError(422, "missing_requirements", "Retrieve dispatch requirements first.")
        details = (
            ("check_availability", self._availability(item)),
            ("get_estimated_arrival", Arrival(contractor_id=item.id, minutes=item.arrival_minutes)),
            ("get_contractor_performance", self._performance(item)),
        )
        if any(self.observations.get((name, contractor_id)) != current for name, current in details):
            raise IntakeError(422, "missing_or_stale_evidence", "Collect current contractor details before proposing.")
        if not details[0][1].eligible:
            raise IntakeError(422, "ineligible", "The contractor is currently ineligible.")
        cap = requirements.arrival_limit_minutes
        if cap is not None and details[1][1].minutes > cap:
            raise IntakeError(422, "arrival_exceeds_sla", "The estimate exceeds the applicable arrival limit.")
        now = self.store.now()
        alternatives = [
            item for item in self.store.contractors(self.actor)
            if item.id != contractor_id and item.id in self.discovered
            and eligible(item, self.zip_code, self.trade, now)
            and (cap is None or item.arrival_minutes <= cap)
        ]
        alternatives.sort(key=lambda item: (item.arrival_minutes, -item.completion_rate, -item.rating, item.id))
        return ProposalDraft(
            contractor_id=contractor_id, alternative_ids=[candidate.id for candidate in alternatives[:2]],
            arrival_minutes=item.arrival_minutes,
            fingerprints={record.id: digest(record.model_dump_json()) for record in [item, *alternatives[:2]]},
        )
