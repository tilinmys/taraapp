import { router, type Href } from 'expo-router';
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import type { IconName } from '@/components/system/icon';
import type { Direction } from '@/features/onboarding/flow';
import { STRINGS, format, type Lang, type StringKey } from '@/i18n/strings';

// App session state: language, role and the current screen's primary action.
// Mounted at the app root (app/_layout.tsx) so onboarding and the workspaces
// share it, and switching language or going back never loses a choice.

export type Role = 'asha' | 'mother' | 'doctor';

/** What the sticky bottom button shows and does on the current screen. */
export type Cta = {
  label: string;
  enabled: boolean;
  onPress: () => void;
  /** Optional low-emphasis text link under the button (never a 2nd button). */
  secondary?: { label: string; onPress: () => void };
  /** Optional leading icon; changing it morphs in place. */
  icon?: IconName;
  /** Haptic weight on press (default heavy). */
  haptic?: 'heavy' | 'medium' | 'success' | 'warning';
  /** One quiet line above the button with the specifics (who / what). */
  context?: string;
  /** Live status shown in the bar above the button (e.g. voice activity). */
  dock?: ReactNode;
};

type OnboardingState = {
  language: Lang;
  setLanguage: (l: Lang) => void;
  role: Role | null;
  setRole: (r: Role) => void;
  cta: Cta | null;
  setCta: (c: Cta | null) => void;
  sheetOpen: boolean;
  setSheetOpen: (open: boolean) => void;
  /** Direction of the current/next page transition. */
  direction: Direction;
  setDirection: (d: Direction) => void;
  /** Name given at sign-up (kept on this device for the session only). */
  profileName: string;
  setProfileName: (name: string) => void;
  /** Demo: which sample mother the mother workspace shows. */
  demoMother: 'lakshmi' | 'kavitha';
  setDemoMother: (id: 'lakshmi' | 'kavitha') => void;
};

const Ctx = createContext<OnboardingState | null>(null);

export function OnboardingProvider({ children }: { children: ReactNode }) {
  const [language, setLanguage] = useState<Lang>('kn');
  // Smart default: ASHA workers are the primary users, so the role screen
  // never opens blank or with a disabled button.
  const [role, setRole] = useState<Role | null>('asha');
  const [cta, setCta] = useState<Cta | null>(null);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [direction, setDirection] = useState<Direction>(1);
  const [profileName, setProfileName] = useState('');
  const [demoMother, setDemoMother] = useState<'lakshmi' | 'kavitha'>('lakshmi');
  const value = useMemo(
    () => ({
      language,
      setLanguage,
      role,
      setRole,
      cta,
      setCta,
      sheetOpen,
      setSheetOpen,
      direction,
      setDirection,
      profileName,
      setProfileName,
      demoMother,
      setDemoMother,
    }),
    [cta, demoMother, direction, language, profileName, role, sheetOpen],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useOnboarding() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useOnboarding must be used inside OnboardingProvider (app/_layout.tsx)');
  return ctx;
}

/** Translate a key into the selected language, filling {placeholders}. */
export function useT() {
  const { language } = useOnboarding();
  return useCallback(
    (key: StringKey, values?: Record<string, string | number>) => {
      const s = STRINGS[language][key];
      return values ? format(s, values) : s;
    },
    [language],
  );
}

/** Register the sticky CTA for the current screen (label, enabled, action). */
export function useOnboardingCta(
  label: string,
  enabled: boolean,
  onPress: () => void,
  secondary?: Cta['secondary'],
) {
  const { setCta } = useOnboarding();
  const secLabel = secondary?.label;
  const secPress = secondary?.onPress;
  useEffect(() => {
    setCta({
      label,
      enabled,
      onPress,
      secondary: secLabel && secPress ? { label: secLabel, onPress: secPress } : undefined,
    });
  }, [enabled, label, onPress, secLabel, secPress, setCta]);
}

/**
 * Step navigation. The direction is committed first, so the page that is
 * leaving re-renders with the matching exit animation, then the route
 * changes on the next frame.
 */
export function useOnboardingNav() {
  const { setDirection } = useOnboarding();
  const forward = useCallback(
    (to: Href) => {
      setDirection(1);
      requestAnimationFrame(() => router.push(to));
    },
    [setDirection],
  );
  const back = useCallback(() => {
    setDirection(-1);
    requestAnimationFrame(() => {
      if (router.canGoBack()) router.back();
      else router.replace('/welcome');
    });
  }, [setDirection]);
  return { forward, back };
}
