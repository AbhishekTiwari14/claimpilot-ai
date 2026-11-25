export type ClaimRow = {
  claim_id: string; conversation_id: string; status: string; version: number;
  damage_type: string | string[] | null; dispatch_status: string | null;
};
export type Fact = { value: string | string[]; status: string };
export type Claim = Omit<ClaimRow, 'damage_type' | 'dispatch_status'> & {
  facts: Record<string, Fact>; conflicts: Record<string, Fact[]>; missing_fields: string[];
  pending_message_id: string | null; processing_error: string | null;
  photo_follow_ups: { photo_id: string; question: string; kind: string }[];
};
export type Message = { id: string; sequence: number; role: 'assistant' | 'customer'; text: string; created_at: string };
export type History = { claim_id: string; messages: Message[]; next_after: number | null };
export type Turn = { claim: Claim; reply: Message };
export type Photo = { id: string; width: number; height: number; created_at: string };
export type Observation = {
  image_id: string; image_quality: string; limitations: string[]; follow_up_questions: string[];
  visible: { observed_damage_types: string[]; affected_areas: string[]; materials: string[]; visible_indicators: string[] };
  interpretation: { apparent_severity: string; potential_safety_concerns: string[]; suggested_specializations: string[] };
};
export type Provider = 'offline_fixture' | 'groq';
export type Analysis = { id: string; status: string; photo_ids: string[]; provider: Provider; model: string;
  result: { observations: Observation[]; batch_limitations: string[] } | null; error_code: string | null };
export type Scope = { document_type: 'policy' | 'operations'; policy_id?: string | null; policy_version?: string | null };
export type DocumentInput = Scope & { title: string; subject_claimant_id?: string | null; document_version?: number };
export type DocumentRow = DocumentInput & { id: string; filename: string; status: string; generation: number;
  created_at: string; error_code: string | null };
export type Source = {
  chunk_id: string; filename: string; title: string; section: string | null; page: number | null;
  distance: number; text: string; policy_id: string | null; policy_version: string | null;
};
export type Citation = { category: string; excerpt: string; source: Source };
export type Answer = { answer: string; outcome: string; insufficient_evidence: boolean; provider: Provider;
  citations: Citation[]; retrieved: Source[]; uncertainties: string[] };
export type Assessment = { policy_id: string; policy_version: string; assessment: Answer };
export type Urgency = { level: string; mandatory_escalation: boolean; reasons: string[] };
export type ClaimDetail = { claim: Claim; urgency: Urgency; analyses: Analysis[]; workflow_ids: string[]; policy_version: string | null };
export type Workflow = {
  workflow_id: string; claim_version: number; status: string; reason: string | null; pause_id: string | null;
  policy_outcome?: string | null; policy_citations?: string[]; dispatch_citations?: string[];
  contractor_run_id?: string | null; proposal_id?: string | null; proposal_version?: number | null;
  transitions: { node: string; status: string; reason: string | null }[]; execution_error: string | null;
};
export type RequirementSource = { chunk_id: string; filename: string; section: string | null; excerpt: string };
export type SLA = { provenance: string; acknowledgement_limit_minutes: number; arrival_limit_minutes: number | null; sources: RequirementSource[] };
export type Proposal = {
  id: string; version: number; status: string; stale: boolean; urgency: Urgency; explanation: string;
  recommended_contractor_id: string | null; alternative_contractor_ids: string[];
  required_specialization: string | null; estimated_arrival_minutes: number | null;
  requirements: SLA | null; uncertainties: string[];
};
export type Run = { provider: Provider; model: string; proposal: Proposal | null };
export type Trace = {
  decision_summary: string | null;
  tools: { call_id: string | null; tool: string; arguments: Record<string, unknown>; result: Record<string, unknown>;
    duration_ms: number; attempts: number; success: boolean; iteration: number }[];
  model_turns: { iteration: number; usage: Record<string, number>; duration_ms: number; error: string | null }[];
};
export type Dispatch = {
  id: string; contractor_id: string; proposal_version: number; status: string; sla_outcome: string;
  sent_at: string; acknowledgement_deadline: string; resolved_at: string | null; reviewer_id: string;
  decided_at: string; decision_reason: string; sla: SLA; policy_citations: string[];
};
export type DispatchSummary = { workflow: Workflow; dispatches: Dispatch[] };
export type Contractor = {
  id: string; name: string; specializations: string[]; service_areas: string[]; zip_codes: string[];
  active: boolean; available: boolean; capacity: number; rating: number; arrival_minutes: number;
  opens_utc: number; closes_utc: number; emergency_capable: boolean; credentialed: boolean; insured: boolean;
};

const base = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '');
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export function client(token: string) {
  async function request<T>(path: string, method = 'GET', body?: unknown, key?: string, blob = false): Promise<T> {
    const headers = new Headers({ Authorization: `Bearer ${token}` });
    if (key) headers.set('Idempotency-Key', key);
    if (body !== undefined && !(body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(base + path, {
      method, headers, cache: 'no-store',
      body: body instanceof FormData ? body : body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new ApiError(response.status, data.code || data.error_code || 'request_failed',
        data.message || (data.error_code ? data.error_code.replaceAll('_', ' ') : `Request failed (${response.status}).`));
    }
    if (response.status === 204) return undefined as T;
    return blob ? await response.blob() as T : response.json();
  }
  return {
    get: <T>(path: string) => request<T>(path),
    post: <T>(path: string, body?: unknown, key?: string) => request<T>(path, 'POST', body, key),
    delete: (path: string, key: string) => request<void>(path, 'DELETE', undefined, key),
    blob: (path: string) => request<Blob>(path, 'GET', undefined, undefined, true),
  };
}
