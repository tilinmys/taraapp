"use client";

/*
 * SakhiHeader - top status strip (~10% viewport) for the redesigned rural
 * call screen (SakhiLiveSession.tsx, 2026-08-30).
 *
 * Pure presentation: every value is a prop, no LiveKit/data-fetching of its
 * own, so it renders identically in a story/screenshot as it does live.
 */

// Owned here rather than imported from SakhiLiveSession.tsx, which imports
// SakhiHeader itself - keeping the shared literal-union types defined at
// whichever end has no reverse dependency avoids a type-only import cycle
// between the two files.
export type LangKey = "kannada" | "english" | "hindi";
export type TriageRisk = "ROUTINE" | "OBSERVATION" | "MODERATE" | "HIGH_RISK";

const LANGUAGE_LABEL: Record<LangKey, string> = {
  kannada: "ಕನ್ನಡ",
  english: "English",
  hindi: "हिंदी",
};

// Extends the brief's ROUTINE/HIGH_RISK-only pill spec with the two
// intermediate levels TriageEngine actually returns (sakhi_triage.py) -
// dropping MODERATE/OBSERVATION here would misrepresent a real risk state
// as "nothing to show", which is worse than an extra two branches.
const RISK_PILL: Record<TriageRisk, { label: string; className: string }> = {
  ROUTINE: {
    label: "ಸಾಮಾನ್ಯ",
    className: "bg-emerald-100 text-emerald-800 border border-emerald-300",
  },
  OBSERVATION: {
    label: "ಗಮನಿಸಿ",
    className: "bg-amber-100 text-amber-800 border border-amber-300",
  },
  MODERATE: {
    label: "ಮಧ್ಯಮ ಅಪಾಯ",
    className: "bg-amber-100 text-amber-900 border border-amber-400",
  },
  HIGH_RISK: {
    label: "ತುರ್ತು",
    className: "bg-rose-100 text-rose-900 border border-rose-400 animate-pulse",
  },
};

export function SakhiHeader({
  language,
  motherName,
  motherAge,
  triageRisk,
}: {
  language: LangKey;
  motherName?: string;
  motherAge?: number;
  triageRisk: TriageRisk;
}) {
  const pill = RISK_PILL[triageRisk];
  const centerLabel = motherName
    ? `${motherName}${motherAge ? ` (${motherAge}y)` : ""}`
    : "ಹೊಸ ತಪಾಸಣೆ";

  return (
    <header className="flex h-[10dvh] min-h-14 w-full shrink-0 items-center justify-between gap-2 px-1">
      <span className="shrink-0 rounded-full border border-forest/20 bg-white/70 px-3 py-1 text-sm font-semibold text-forest sm:text-base">
        {LANGUAGE_LABEL[language]}
      </span>

      <p className="min-w-0 flex-1 truncate text-center text-base font-bold text-forest sm:text-lg">
        {centerLabel}
      </p>

      <span
        className={`shrink-0 rounded-full px-3 py-1 text-xs font-bold tracking-wide sm:text-sm ${pill.className}`}
        role="status"
      >
        {pill.label}
      </span>
    </header>
  );
}
