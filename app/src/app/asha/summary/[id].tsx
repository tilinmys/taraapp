import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
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
import { ANTENATAL_QUESTIONS, RULES, type RuleCode } from '@/data/mockScreening';
import { store, useMother } from '@/data/store';

// Check-in, step 3 of 3: the report. While the answers are processed, a
// Dynamic-Island-style pill shows each stage at the top; then the report
// builds grid by grid, most important first:
//   1 risk + where it was sent   2 vitals with trends   3 rule audit
//   4 the 24 answers at a glance  5 the live transcript  6 signature + PDF
// "Finish" returns to the mother list (back does the same).

const STEPS = ['Transcribing audio', 'Extracting 24 clinical markers', 'Evaluating pre-eclampsia rules'];
const STEP_MS = 900;

export default function Summary() {
  const { id, report: reportId } = useLocalSearchParams<{ id: string; report?: string }>();
  const mother = useMother(id);
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
  const answered = report ? Object.keys(report.answers).length : 0;
  const top = rules.find((c) => c === 'PE-04') ?? rules.find((c) => RULES[c].severity !== 'watch');
  const dangers = report ? ANTENATAL_QUESTIONS.filter((q) => report.answers[q.id]?.danger).length : 0;

  return (
    <Screen
      eyebrow={`Check-in · ${mother.name}`}
      title={report ? 'Check-in report' : 'Preparing report…'}
      compactTitle="Report"
      back={{ label: mother.name.split(' ')[0], onPress: finish }}
      cta={{
        label: 'Finish',
        context: report ? `${mother.name} · ${report.high ? 'Sent to Dr. Anand as high risk' : 'Saved as routine'}` : 'Preparing…',
        enabled: !!report,
        icon: 'checkCircle',
        haptic: 'success',
        onPress: finish,
      }}
      overlay={<StatusPill visible={!done || step === STEPS.length} done={done} label={done ? 'Report ready' : STEPS[step]} />}
    >
      {!report ? (
        <View style={styles.steps}>
          {STEPS.map((s, i) => (
            <View key={s} style={styles.stepRow}>
              <Icon name={i < step ? 'checkCircle' : 'wave'} size={22} color={i <= step ? Colors.cherry : Colors.inkSubtle} active={i < step} />
              <AppText variant={i === step ? 'label' : 'body'} color={i <= step ? Colors.ink : Colors.inkMuted}>
                {s}
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
              label={report.high ? 'High risk · refer today' : 'Routine'}
              value={report.high && top ? RULES[top].title : 'All looks well'}
              note={
                report.high
                  ? `${mother.name} is now first in Dr. Anand's queue. Help her reach the PHC today.`
                  : 'No danger signs today. Next check-in in 2 weeks.'
              }
            />
          </Animated.View>

          {/* 2 Vitals */}
          <Animated.View entering={rise(1)} style={styles.section}>
            <AppText variant="overline" color={Colors.inkMuted}>
              VITALS
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
              <BentoCell tone="pumpkin" icon="checklist" label="Answered" value={`${answered}/24`} minHeight={0} />
              <BentoCell
                tone={dangers ? 'alert' : 'sage'}
                icon={dangers ? 'warning' : 'checkCircle'}
                label="Danger signs"
                value={`${dangers}`}
                minHeight={0}
              />
            </BentoRow>
          </Animated.View>

          {/* 5 Transcript */}
          <Animated.View entering={rise(4)}>
            <TranscriptCard lines={report.transcript} title="Live transcript" />
          </Animated.View>

          {/* 6 Signature + PDF */}
          <Animated.View entering={rise(5)} style={styles.sign}>
            <Icon name="signature" size={26} color={Colors.cherry} tone={Colors.pumpkin} />
            <View style={styles.flex}>
              <AppText variant="bodyStrong">{`Recorded by ${report.asha} · ASHA`}</AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                Sent to Dr. Anand, Hosahalli PHC. The doctor sees this same report.
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
  sign: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 16, borderRadius: 20, borderWidth: 1, borderColor: Colors.hairline },
});
