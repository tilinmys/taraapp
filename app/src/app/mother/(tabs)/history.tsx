import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition, useReducedMotion } from 'react-native-reanimated';

import { FilterDropdown, type FilterOption } from '@/components/mother/filter-dropdown';
import { VisitCard, type VisitDoc } from '@/components/mother/visit-card';
import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { type EmblemTone } from '@/components/system/emblem';
import { Icon } from '@/components/system/icon';
import { MissingMother } from '@/components/mother/missing-mother';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { Colors } from '@/constants/theme';
import { useMother, type CheckInReport, type MotherRecord } from '@/data/store';
import { dateParts, VISITS, type Visit } from '@/data/visits';
import { useOnboarding, useT } from '@/features/onboarding/context';
import type { StringKey } from '@/i18n/strings';
import { shareReportPdf } from '@/lib/report-pdf';
import { sharePrescription, shareVisitSummary } from '@/lib/visit-pdf';

// My history (mother). Layout from the sketch:
//   title -> filter drop-down (History / Prescriptions) -> one compact card per
//   visit: what, who, when, next visit and the PDF. Readings stay in the PDF.
// History: every visit and ASHA check-in, newest first, grouped by month.
// Prescriptions: only visits with medicines. Medicine names never appear
// on screen (privacy on a shared phone); they are inside the PDF.

type Filter = 'history' | 'prescriptions';

type Item = {
  key: string;
  date: string; // ISO
  title: string;
  who: string;
  tone: EmblemTone;
  icon: AppIconName;
  next?: string;
  docs: VisitDoc[];
};

// Demo "today" (the sample data is dated around it).
const TODAY = '2026-10-21';

/** Mother-facing names: plain and calm ("Referral" reads as alarming). */
const VISIT_LOOK: Record<Visit['kind'], { title: StringKey; icon: AppIconName }> = {
  'Check-up': { title: 'kindCheckup', icon: 'clinic' },
  Scan: { title: 'kindScan', icon: 'kicks' },
  'Lab tests': { title: 'kindLab', icon: 'report' },
  Referral: { title: 'kindDoctor', icon: 'clinic' },
};
type T = (key: StringKey, values?: Record<string, string | number>) => string;

/** ASHA check-in times are relative ("Yesterday, 16:10"); place them on the calendar. */
function isoFromRelative(when: string) {
  const base = new Date(`${TODAY}T12:00:00`);
  const days = /^(Today|Just now)/.test(when) ? 0 : /^Yesterday/.test(when) ? 1 : Number(when.match(/(\d+)\s*day/)?.[1] ?? 0);
  base.setDate(base.getDate() - days);
  return base.toISOString().slice(0, 10);
}

const shortDate = (iso: string) => {
  const d = dateParts(iso);
  return `${d.day} ${d.month}`;
};

function visitDocs(m: MotherRecord, v: Visit, only: Filter, t: T): VisitDoc[] {
  const rx: VisitDoc | null = v.prescription
    ? { id: `${v.id}-rx`, title: t('histRxOne'), meta: t('histFromDoctor'), kind: 'prescription', share: () => sharePrescription(m, v) }
    : null;
  if (only === 'prescriptions') return rx ? [rx] : [];
  const summary: VisitDoc = { id: `${v.id}-sum`, title: t('histSummary'), meta: t('histSummaryMeta'), kind: 'report', share: () => shareVisitSummary(m, v) };
  return rx ? [summary, rx] : [summary];
}

function reportItem(m: MotherRecord, r: CheckInReport, t: T): Item {
  return {
    key: r.id,
    date: isoFromRelative(r.when),
    title: t('histCheckIn'),
    who: `${r.asha} · ${t('histAtHome')}`,
    tone: 'pumpkin',
    icon: 'asha',
    next: m.nextVisit,
    docs: [{ id: `${r.id}-pdf`, title: t('histReport'), meta: t('histYourAnswers'), kind: 'report', share: () => shareReportPdf(m, r) }],
  };
}

function buildItems(m: MotherRecord, filter: Filter, t: T): Item[] {
  const visits = VISITS.filter((v) => filter === 'history' || v.prescription).map<Item>((v) => ({
    key: v.id,
    date: v.date,
    title: filter === 'prescriptions' ? t('histRxOne') : t(VISIT_LOOK[v.kind].title),
    who: v.doctor.includes(v.place) ? v.doctor : `${v.doctor} · ${v.place}`,
    tone: v.tone,
    icon: filter === 'prescriptions' ? 'prescription' : VISIT_LOOK[v.kind].icon,
    next: v.next ? shortDate(v.next.date) : undefined,
    docs: visitDocs(m, v, filter, t),
  }));
  const checkIns = filter === 'history' ? m.reports.map((r) => reportItem(m, r, t)) : [];
  return [...checkIns, ...visits].sort((a, b) => b.date.localeCompare(a.date));
}
export default function History() {
  const { demoMother, language } = useOnboarding();
  const t = useT();
  const mother = useMother(demoMother);
  const reduceMotion = useReducedMotion();
  const [filter, setFilter] = useState<Filter>('history');
  const [tip, setTip] = useState(true);
  if (!mother) return <MissingMother />;

  const items = buildItems(mother, filter, t);
  const options: FilterOption<Filter>[] = [
    {
      id: 'history',
      label: t('histHistory'),
      hint: t('histHistoryHint'),
      icon: 'clipboard',
      tone: 'cherry',
      count: buildItems(mother, 'history', t).length,
    },
    {
      id: 'prescriptions',
      label: t('histRx'),
      hint: t('histRxHint'),
      icon: 'pill',
      tone: 'pumpkin',
      count: VISITS.filter((v) => v.prescription).length,
    },
  ];
  // Group by month ("October 2026"), newest first.
  const groups = items.reduce<{ month: string; items: Item[] }[]>((acc, it) => {
    const month = dateParts(it.date, language).monthYear;
    const last = acc[acc.length - 1];
    if (last?.month === month) last.items.push(it);
    else acc.push({ month, items: [it] });
    return acc;
  }, []);

  return (
    <Screen
      inTabs
      eyebrow={`${mother.name} · ${mother.village}`}
      title={t('histTitle')}
      titleVariant="greeting"
      compactTitle={filter === 'history' ? t('histTitle') : t('histRx')}
      right={{ label: t('histProfile'), icon: 'profile', art: 'profile', onPress: () => router.push('/profile') }}
      gap={16}
    >
      <View style={styles.filterRow}>
        <FilterDropdown label={t('histShow')} options={options} value={filter} onChange={setFilter} />
        <AppText variant="caption" color={Colors.inkMuted} style={styles.countText}>
          {filter === 'history' ? (items.length === 1 ? t('histVisit1') : t('histVisitsN', { n: items.length })) : items.length === 1 ? t('histRx1') : t('histRxN', { n: items.length })}
        </AppText>
      </View>

      {tip && (
        <Animated.View exiting={reduceMotion ? undefined : FadeOut.duration(160)} style={styles.tip}>
          <AppIcon name="report" size={40} />
          <View style={styles.flex}>
            <AppText variant="bodyStrong">{t('histTipTitle')}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {t('histTipBody')}
            </AppText>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={t('histHideTip')} hitSlop={8} onPress={() => setTip(false)} style={styles.tipClose}>
            <Icon name="close" size={16} color={Colors.inkMuted} weight="bold" />
          </Pressable>
        </Animated.View>
      )}

      {items.length === 0 ? (
        <StateView
          compact
          title={filter === 'prescriptions' ? t('histNoRx') : t('histNone')}
          body={t('histNoneBody', { asha: mother.asha })}
          icon={filter === 'prescriptions' ? 'pill' : 'clipboard'}
          action={{ label: t('histAskTara'), onPress: () => router.navigate('/mother/talk') }}
        />
      ) : (
        <Animated.View key={filter} entering={reduceMotion ? undefined : FadeIn.duration(220)} style={styles.groups}>
          {groups.map((g) => (
            <Animated.View
              key={g.month}
              layout={reduceMotion ? undefined : LinearTransition.springify().stiffness(250).damping(25)}
              style={styles.group}
            >
              <View style={styles.monthRow}>
                <AppText variant="overline" color={Colors.inkMuted}>
                  {g.month.toUpperCase()}
                </AppText>
                <View style={styles.monthLine} />
              </View>
              {g.items.map((it, i) => {
                const d = dateParts(it.date, language);
                return (
                  <VisitCard
                    key={it.key}
                    index={i}
                    title={it.title}
                    who={it.who}
                    day={d.day}
                    month={d.month}
                    tone={it.tone}
                    next={it.next}
                    docs={it.docs}
                  />
                );
              })}
            </Animated.View>
          ))}
        </Animated.View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  filterRow: { flexDirection: 'row', alignItems: 'center', gap: 12, zIndex: 20, elevation: 20 },
  countText: { marginLeft: 'auto' },
  tip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    padding: 14,
    borderRadius: 20,
    backgroundColor: Colors.orangeTint,
    borderWidth: 1,
    borderColor: `${Colors.pumpkin}22`,
  },
  tipClose: { width: 32, height: 32, borderRadius: 16, alignItems: 'center', justifyContent: 'center', alignSelf: 'flex-start' },
  groups: { gap: 20 },
  group: { gap: 12 },
  monthRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  monthLine: { flex: 1, height: 1, backgroundColor: Colors.hairline },
});
