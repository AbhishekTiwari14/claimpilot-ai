import json

from claimpilot.prompts.knowledge import ASSESSMENT_PROMPT, QUESTION_PROMPT
from claimpilot.schemas.knowledge import (
    AnswerSelection, Citation, Evidence, GroundedAnswer, Operator, PolicyAssessment, QuestionSelection,
    SearchRequest,
)
from claimpilot.services.knowledge_index import KnowledgeIndex
from claimpilot.services.llm import LLMError, LLMService
from claimpilot.storage.intake import IntakeError


def _fixture(query: str, evidence: list[Evidence], assessment: bool) -> dict:
    if not assessment:
        for number, item in enumerate(evidence, 1):
            # The offline example can explicitly ask for a named section.
            if item.section and query.casefold().strip("?") == f"what does {item.section.casefold()} say":
                return {"outcome": "answered", "source_numbers": [number]}
    return {"outcome": "insufficient_evidence", "sources" if assessment else "source_numbers": []}


def _choices(selection: AnswerSelection | QuestionSelection) -> dict[tuple[int, str], None]:
    if isinstance(selection, QuestionSelection):
        return dict.fromkeys((number, "answer") for number in selection.source_numbers)
    return dict.fromkeys((choice.source_number, choice.category) for choice in selection.sources)


def grounded_answer(
    index: KnowledgeIndex, llm: LLMService, actor: Operator, request: SearchRequest,
    *, subject: str = "", facts: dict | None = None,
) -> GroundedAnswer:
    assessment = facts is not None
    evidence = index.search(actor.tenant_id, request, subject=subject)
    provider = "groq" if index.settings.claimpilot_llm_provider == "groq" else "offline_fixture"
    sources = dict(enumerate(evidence, 1))
    citations = []
    outcome = "insufficient_evidence"
    uncertainties = []
    if evidence:
        def validate(selection: AnswerSelection | QuestionSelection) -> None:
            choices = _choices(selection)
            if bool(choices) != (selection.outcome != "insufficient_evidence"):
                raise ValueError("Invalid outcome or empty evidence")
            selected = {number for number, _ in choices}
            if not selected <= sources.keys():
                raise ValueError("Citation is absent from retrieved evidence")
            if selection.outcome == "conflicting_evidence" and len({sources[n].text for n in selected}) < 2:
                raise ValueError("Conflicting evidence requires two distinct excerpts")
            for _, category in choices:
                if assessment == (category == "answer"):
                    raise ValueError("Invalid evidence category")
            required = {"potentially_covered": "coverage", "potentially_excluded": "exclusion"}
            if selection.outcome in required and not any(
                category == required[selection.outcome] for _, category in choices
            ):
                raise ValueError("The preliminary outcome needs corresponding evidence")

        try:
            selection = llm.structured(
                AnswerSelection if assessment else QuestionSelection,
                system_prompt=ASSESSMENT_PROMPT if assessment else QUESTION_PROMPT,
                operation="policy-assessment-v2" if assessment else "knowledge-evidence-v2",
                user_payload=json.dumps({
                    "task": "assessment" if assessment else "question",
                    "question": request.query, "reported_claim_facts": facts,
                    "evidence": [
                        {"source_number": number, "section": e.section, "text": e.text}
                        for number, e in sources.items()
                    ],
                }, ensure_ascii=False),
                validate=validate, offline_result=lambda: _fixture(request.query, evidence, assessment),
            )
        except LLMError as error:
            if error.code != "invalid_output":
                raise IntakeError(503, "answer_unavailable", "Grounded answer generation is temporarily unavailable.") from None
            uncertainties.append("The proposed supporting evidence did not pass citation validation.")
        else:
            outcome = selection.outcome
            citations = [
                Citation(category=category, excerpt=sources[number].text, source=sources[number])
                for number, category in _choices(selection)
            ]
        evidence = index.revalidate(actor.tenant_id, request, evidence, subject)
        active = {item.chunk_id for item in evidence}
        if any(citation.source.chunk_id not in active for citation in citations):
            outcome, citations = "insufficient_evidence", []
            uncertainties.append("The active source set changed during answer generation.")
    if outcome == "conflicting_evidence":
        uncertainties.append("The selected clauses conflict. Their applicability or precedence requires review.")
    if assessment:
        uncertainties.append("This assessment uses reported facts and retrieved clauses. Applicability requires human review.")
        if not any(citation.category == "deductible" for citation in citations):
            uncertainties.append("No deductible was supported by the selected policy evidence.")
    if outcome == "insufficient_evidence":
        uncertainties.append("The authorized sources did not provide enough supporting evidence.")
    # Respond with source language itself. No unsupported generated narrative is published.
    answer = "\n\n".join(dict.fromkeys(c.excerpt for c in citations)) if citations else "Insufficient evidence."
    return GroundedAnswer(
        answer=answer, outcome=outcome, insufficient_evidence=outcome == "insufficient_evidence",
        citations=citations, retrieved=evidence, uncertainties=uncertainties,
        recommended_human_review=assessment or outcome == "conflicting_evidence", provider=provider,
    )


def assess_claim(index: KnowledgeIndex, llm: LLMService, actor: Operator, claim_id) -> PolicyAssessment:
    scope, subject, confirmed = index.store.claim_policy(actor, claim_id)
    if scope is None:
        raise IntakeError(409, "policy_unbound", "Bind an authorized policy version before assessing the claim.")
    facts = {key: value for key, value in confirmed.items()
             if key in ("damage_type", "suspected_cause", "affected_areas", "ongoing_hazards")}
    focus = " ".join(str(value)[:100] for value in facts.values())
    request = SearchRequest(
        **scope.model_dump(), query=f"{focus} coverage exclusions deductible limitations", top_k=8,
    )
    outcome = grounded_answer(index, llm, actor, request, subject=subject, facts=facts)
    if index.store.claim_policy(actor, claim_id)[0] != scope:
        raise IntakeError(409, "policy_changed", "The policy binding changed during assessment.")
    groups = {kind: [c for c in outcome.citations if c.category == kind]
              for kind in ("coverage", "exclusion", "deductible", "limitation")}
    return PolicyAssessment(
        claim_id=claim_id, policy_id=scope.policy_id, policy_version=scope.policy_version,
        assessment=outcome, coverage_clauses=groups["coverage"], exclusions=groups["exclusion"],
        deductibles=groups["deductible"], limitations=groups["limitation"],
    )
