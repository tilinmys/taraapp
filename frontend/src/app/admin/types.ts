// Shapes returned by status_server.py's /dashboard/* endpoints
// (sakhi_dashboard_store.py). Kept in one place so the dashboard and the
// printable report card cannot disagree about a field name.

export type Component = {
  component: string;
  last_status: "ok" | "warn" | "error" | string;
  last_label: string | null;
  last_message: string | null;
  last_session: string | null;
  last_error: string | null;
  last_error_at: string | null;
  last_error_session: string | null;
  last_ok_at: string | null;
  error_count: number;
  updated_at: string;
};

export type Summary = {
  backend: string;
  mothers: number;
  high_risk_mothers: number;
  calls: number;
  escalations: number;
  errors: number;
  components: Component[];
};

export type Mother = {
  patient_id: string;
  name: string;
  age: number | null;
  village: string | null;
  phone: string | null;
  gestational_weeks: number | null;
  risk_status: string;
  danger_signs: string[];
  updated_at: string;
};

export type Turn = {
  session_id: string;
  turn_no: number;
  speaker: "asha" | "sakhi" | string;
  text: string;
  language: string | null;
  ts: string;
};

export type CallRow = {
  id: number;
  session_id: string | null;
  language: string | null;
  risk_level: string | null;
  intake_answered: number;
  intake_total: number;
  escalated: number;
  duration_ms: number | null;
  created_at: string;
  turns: Turn[];
};

export type PatientReport = {
  mother: Omit<Mother, "danger_signs">;
  danger_signs: string[];
  bp_readings: { systolic: number; diastolic: number; risk_level: string; recorded_at: string }[];
  symptom_history: { recorded_at: string; present: string[] }[];
  teleconsults: { channel_id: string; reason: string | null; risk_level: string | null; trigger_sign: string | null; created_at: string }[];
  calls: CallRow[];
  generated_at: string;
};

export type PipelineEvent = {
  id: number;
  session_id: string | null;
  ts: string;
  stage: string;
  status: string;
  label: string;
  message: string | null;
  event: string | null;
};

export const RISK_LABEL: Record<string, string> = {
  HIGH_RISK: "High risk",
  MODERATE: "Moderate",
  OBSERVATION: "Observation",
  ROUTINE: "Routine",
};

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}
