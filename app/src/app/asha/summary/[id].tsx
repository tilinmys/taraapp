import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeInDown, useReducedMotion } from 'react-native-reanimated';

import { BentoCell, BentoRow } from '@/components/report/bento';
import { PdfButton } from '@/components/report/pdf-button';
import { saveReportPdf } from '@/voice/report-file';
import { RuleAudit } from '@/components/report/rule-audit';
import { TranscriptCard } from '@/components/report/transcript-card';
import { VitalsGrid } from '@/components/report/vitals-grid';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { StatusPill } from '@/components/system/status-pill';
import { AppText } from '@/components/ui/app-text';
import { Colors, Curves, Haptic, Layout, Nav } from '@/constants/theme';
import { needsClinical } from '@/data/clinical';
import { dayStart } from '@/data/care-network';
import { questionCount, RULES, type RuleCode } from '@/data/mockScreening';
import { store, useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { tx } from '@/i18n/content';
import type { StringKey } from '@/i18n/strings';

// Check-in, step 3 of 3: the report. While the answers are processed, a
// Dynamic-Island-style pill shows each stage at the top; then the report
// builds grid by grid, most important first:
//   1 risk + where it was sent   2 vitals with trends   3 rule audit
//   4 the answers at a glance     5 the live transcript  6 signature + PDF
// Measurements (BP, weight, Hb) are entered by the ASHA, not asked by
// Sakhi: until today's are in, a card asks for them (risk needs the BP).
// "Finish" returns to the mother list (back does the same).

const STEPS: StringKey[] = ['smStep1', 'smStep2', 'smStep3'];
const STEP_MS = 900;

export default function Summary() {
  const { id, report: reportId } = useLocalSearchParams<{ id: string; report?: string }>();
  const mother = useMother(id);
  const t = useT();
  const { language } = useOnboarding();
  const reduceMotion = useReducedMotion();
  const [step, setStep] = useState(0); // 0..STEPS.length (= done)

  useEffect(() => {
    const timers = STEPS.map((_, i) => setTimeout(() => setStep(i + 1), STEP_MS * (i + 1)));
    return () => timers.forEach(clearTimeout);
  }, []);

  const done = step >= STEPS.length;
  // completeCheckIn is idempotent (keyed by the report id), so StrictMode,
  // remounts or a reload can never file the check-in twice.
  useEffect(() => {
    if (done && id && reportId) store.completeCheckIn(id, reportId);
  }, [done, id, reportId]);

  // Only ever the report this check-in filed, never a stale earlier one.
  // Opened without one (old link): her latest report.
  const report = !done ? undefined : reportId ? mother?.reports.find((r) => r.id === reportId) : mother?.reports[0];
  useEffect(() => {
    if (!report) return;
    if (report.high) Haptic.warning();
    else Haptic.success();
  }, [report]);
  // The PDF is made on the phone as soon as the report exists (offline too).
  useEffect(() => {
    if (report && mother) saveReportPdf(mother, report);
  }, [report, mother]);

  if (!mother) return null;
  // Back to her page: the next step (book a visit, checklist) is there.
  const finish = () => router.dismissTo({ pathname: '/asha/mother/[id]', params: { id: mother.id } });
  const rise = (i: number) => (reduceMotion ? undefined : FadeInDown.duration(Nav.rise).delay(Math.min(i, Nav.maxStagger) * Nav.stagger).easing(Curves.settle));
  const rules = (report?.rules ?? []) as RuleCode[];
  const answered = report ? Object.keys(report.answers).filter((k) => k !== 'close').length : 0;
  const top = rules.find((c) => c === 'PE-04') ?? rules.find((c) => RULES[c].severity !== 'watch');
  const dangers = report ? Object.values(report.answers).filter((a) => a?.danger).length : 0;
  const needMeasures = needsClinical(mother.clinical, dayStart(0));

  return (
    <Screen
      eyebrow={t('smEyebrow', { name: mother.name })}
      title={report ? t('smTitle') : t('smPreparing')}
      compactTitle={t('smShort')}
      back={{ label: mother.name.split(' ')[0], onPress: finish }}
      cta={{
        label: t('smFinish'),
        context: report ? `${mother.name} · ${report.high ? t('smSentHigh') : t('smSavedRoutine')}` : t('smPreparing'),
        enabled: !!report,
        icon: 'checkCircle',
        haptic: 'success',
        onPress: finish,
      }}
      overlay={<StatusPill visible={!done || step === STEPS.length} done={done} label={done ? t('smReady') : t(STEPS[step])} />}
    >
      {!report ? (
        <View style={styles.steps}>
          {STEPS.map((s, i) => (
            <View key={s} style={styles.stepRow}>
              <Icon name={i < step ? 'checkCircle' : 'wave'} size={22} color={i <= step ? Colors.cherry : Colors.inkSubtle} active={i < step} />
              <AppText variant={i === step ? 'label' : 'body'} color={i <= step ? Colors.ink : Colors.inkMuted}>
                {t(s)}
              </AppText>
            </View>
          ))}
        </View>
      ) : (
        <>
          {/* 1 Risk */}
          <Animated.View entering={rise(0)}>
            <BentoCell
              tone={report.high ? 'alert' : 'sage'}
              icon={report.high ? 'warning' : 'shield'}
              label={report.high ? t('smHigh') : t('smRoutine')}
              value={report.high && top ? tx(RULES[top].title, language) : t('smAllWell')}
              note={report.high ? t('smHighNote', { name: mother.name }) : t('smRoutineNote')}
            />
          </Animated.View>

          {/* Measurements are the ASHA's: ask for them until today's BP is in. */}
          {needMeasures && (
            <Pressable
              accessibilityRole="button"
              onPress={() => router.push({ pathname: '/asha/clinical/[id]', params: { id: mother.id } })}
              style={({ pressed }) => [styles.measure, pressed && styles.pressed]}
            >
              <Icon name="warning" size={22} color={Colors.pumpkinInk} />
              <AppText variant="bodyStrong" style={styles.flex}>
                {t('clinNeedBp')}
              </AppText>
              <AppText variant="buttonSmall" color={Colors.cherry}>
                {t('clinEnter')}
              </AppText>
            </Pressable>
          )}

          {/* 2 Vitals */}
          <Animated.View entering={rise(1)} style={styles.section}>
            <AppText variant="overline" color={Colors.inkMuted}>
              {t('smVitals')}
            </AppText>
            <VitalsGrid vitals={mother.vitals} />
          </Animated.View>

          {/* 3 Rules */}
          {rules.length > 0 && (
            <Animated.View entering={rise(2)}>
              <RuleAudit rules={rules} answers={report.answers} />
            </Animated.View>
          )}

          {/* 4 Answers at a glance */}
          <Animated.View entering={rise(3)}>
            <BentoRow>
              <BentoCell tone="pumpkin" icon="checklist" label={t('smAnswered')} value={`${answered}/${questionCount(report.visit ?? 1)}`} minHeight={0} />
              <BentoCell
                tone={dangers ? 'alert' : 'sage'}
                icon={dangers ? 'warning' : 'checkCircle'}
                label={t('smDangers')}
                value={`${dangers}`}
                minHeight={0}
              />
            </BentoRow>
          </Animated.View>

          {/* 5 Transcript */}
          <Animated.View entering={rise(4)}>
            <TranscriptCard lines={report.transcript} title={t('smTranscript')} />
          </Animated.View>

          {/* 6 Signature + PDF */}
          <Animated.View entering={rise(5)} style={styles.sign}>
            <Icon name="signature" size={26} color={Colors.cherry} tone={Colors.pumpkin} />
            <View style={styles.flex}>
              <AppText variant="bodyStrong">{t('smRecorded', { name: report.asha })}</AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                {t('smSent')}
              </AppText>
            </View>
          </Animated.View>
          <Animated.View entering={rise(6)}>
            <PdfButton mother={mother} report={report} />
          </Animated.View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  section: { gap: 8 },
  steps: { gap: 14, padding: 18, borderRadius: Layout.radius, backgroundColor: Colors.shell },
  stepRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  pressed: { opacity: 0.8 },
  measure: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, borderRadius: 16, borderWidth: 1.5, borderColor: Colors.pumpkin, backgroundColor: Colors.orangeTint },
  sign: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16, borderRadius: 20, borderWidth: 1, borderColor: Colors.hairline },
});
