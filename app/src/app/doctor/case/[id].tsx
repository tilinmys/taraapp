import { useLocalSearchParams } from 'expo-router';
import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeInDown, useReducedMotion, useSharedValue } from 'react-native-reanimated';

import { AdviceEditor, Consultation } from '@/components/doctor/next-steps';
import { DOC, TRIAGE } from '@/components/doctor/ui';
import { DoctorVitals } from '@/components/doctor/vitals';
import { PdfButton } from '@/components/report/pdf-button';
import { RuleAudit } from '@/components/report/rule-audit';
import { TranscriptCard } from '@/components/report/transcript-card';
import { Icon } from '@/components/system/icon';
import { JumpBar, type JumpItem } from '@/components/system/jump-bar';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors, Curves, Haptic, Nav } from '@/constants/theme';
import { MONO } from '@/constants/typography';
import { ME_DOCTOR, fmtWhen, network, staff } from '@/data/care-network';
import { doctorReview, reviewOf, triageOf, useDoctorReview } from '@/data/doctor-review';
import { ANTENATAL_QUESTIONS, RULES, type Question, type RuleCode } from '@/data/mockScreening';
import { riskOf, store, useMother, type MotherRecord } from '@/data/store';
import { goBack } from '@/lib/nav';

// Case record, built for a phone and for a doctor in a hurry. Five sections,
// always reachable from five equal tabs in the header (no sideways scroll):
//   Summary   ONE status banner (what to do), the ASHA's own note when she
//             escalated ("What happened?"), four equal fact tiles, the
//             danger signs, and "Why this risk" (the rule audit) on demand
//   Vitals    four identical tiles; only an out-of-range value is red
//   Report    the ASHA's Sakhi check-in: transcript preview + PDF
//   Plan      what he decides and sends: Doctor's advice (editable steps)
//             and the Consultation (book / request, reschedule, cancel,
//             complete, with its status) - components/doctor/next-steps
//   Answers   all 24, folded until asked for; a row opens its detail
// One sticky action, the one that fits where the case stands:
//   urgent        Confirm referral (sends the advice, marks it reviewed)
//   not reviewed  Mark as reviewed
//   reviewed      done (when she read it, with Undo in the banner)
// She is never pushed to a "next case": Back returns to her list, which
// shows what is still waiting.

const DEFAULT_ADVICE: Record<'high' | 'normal', string[]> = {
  high: [
    'Come to the PHC today so the doctor can check your blood pressure again.',
    'Rest lying on your left side and avoid heavy work until you are seen.',
    'Call 108 at once if the headache gets worse, your vision gets worse, or you have a fit.',
  ],
  normal: [
    'Keep taking your iron and calcium tablets every day.',
    'Eat one extra meal a day with greens, dal and milk.',
    'Your next check-in with your ASHA worker is in 2 weeks.',
  ],
};

const SECTIONS: JumpItem[] = [
  { id: 'summary', label: 'Case', icon: 'warning' },
  { id: 'vitals', label: 'Vitals', icon: 'pulse' },
  { id: 'report', label: 'Report', icon: 'chat' },
  { id: 'plan', label: 'Plan', icon: 'calendar' },
  { id: 'answers', label: 'Answers', icon: 'checklist' },
];

const AnswerRow = memo(function AnswerRow({ q, value, flagged, onOpen }: { q: Question; value: string; flagged: boolean; onOpen: (q: Question) => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${flagged ? 'Danger sign. ' : ''}${q.en} Answer: ${value}`}
      accessibilityHint="Opens the question details"
      onPress={() => {
        Haptic.light();
        onOpen(q);
      }}
      style={({ pressed }) => [styles.answer, flagged && styles.answerFlag, pressed && styles.pressed]}
    >
      <View style={styles.flex}>
        <AppText variant="caption" color={Colors.inkMuted}>
          {q.en}
        </AppText>
        <AppText variant={flagged ? 'bodyStrong' : 'body'} color={flagged ? Colors.cherry : Colors.ink}>
          {value}
        </AppText>
      </View>
      <Icon name="forward" size={16} color={Colors.inkMuted} weight="bold" />
    </Pressable>
  );
});

export default function CaseRecord() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const mother = useMother(id);
  useEffect(() => {
    if (id) doctorReview.opened(id);
  }, [id]);
  if (!mother) return null;
  return <Record key={mother.id} mother={mother} />;
}

function Record({ mother }: { mother: MotherRecord }) {
  const { section } = useLocalSearchParams<{ section?: string }>();
  const review = useDoctorReview();
  const reduceMotion = useReducedMotion();
  const sheet = useSharedValue(0);
  const [row, setRow] = useState<Question | null>(null);
  const [allOpen, setAllOpen] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const closeRow = useCallback(() => setRow(null), []);
  const jump = useRef<((y: number) => void) | null>(null);
  const [offsets, setOffsets] = useState<Record<string, number>>({});
  const jumped = useRef(false);

  // Opened from a report: start on the Report section, once it is laid out.
  useEffect(() => {
    if (jumped.current || section !== 'report' || offsets.report === undefined) return;
    jumped.current = true;
    const y = offsets.report;
    const t = setTimeout(() => jump.current?.(y), 250);
    return () => clearTimeout(t);
  }, [offsets.report, section]);

  const { hits, high } = riskOf(mother);
  const codes = hits.map((h) => h.code as RuleCode);
  const top = codes.find((c) => c === 'PE-04') ?? codes.find((c) => RULES[c].severity !== 'watch');
  const flagged = ANTENATAL_QUESTIONS.filter((q) => mother.answers[q.id]?.danger);
  const report = mother.reports[0];
  // The advice he is writing: starts from what was sent, else a draft for her risk.
  const [steps, setSteps] = useState<string[]>(() => mother.advice ?? DEFAULT_ADVICE[high ? 'high' : 'normal']);
  const advice = steps.map((x) => x.trim()).filter(Boolean);
  const triage = triageOf(mother, review);
  const reviewed = reviewOf(mother, review);
  const tone = TRIAGE[triage];

  const anchor = (key: string) => ({
    onLayout: (e: { nativeEvent: { layout: { y: number } } }) => {
      const y = e.nativeEvent.layout.y;
      setOffsets((o) => (o[key] === y ? o : { ...o, [key]: y }));
    },
  });
  const rise = (i: number) => (reduceMotion ? undefined : FadeInDown.duration(Nav.rise).delay(Math.min(i, Nav.maxStagger) * Nav.stagger).easing(Curves.settle));

  const banner =
    triage === 'urgent'
      ? { over: 'URGENT · ACT TODAY', title: top ? RULES[top].title : `Marked "needs care" by ASHA ${mother.asha}`, line: top ? RULES[top].action : 'Call the ASHA and see her at the PHC today.' }
      : triage === 'referred'
        ? { over: 'REFERRED · FOLLOW UP', title: top ? RULES[top].title : 'Referral sent', line: 'Advice is on her phone. Check she reached the PHC.' }
        : codes.length
          ? { over: 'WATCH', title: RULES[codes[0]].title, line: RULES[codes[0]].action }
          : { over: 'ROUTINE', title: 'No danger signs', line: `Checked ${mother.lastCheck}. Continue routine care.` };

  const cta =
    triage === 'urgent'
      ? {
          label: 'Confirm referral',
          context: `Sends ${advice.length}-step advice to ${mother.name}`,
          enabled: advice.length > 0,
          haptic: 'success' as const,
          icon: 'send' as const,
          onPress: () => {
            store.confirmReferral(mother.id, advice, ME_DOCTOR);
            network.adviceSent(mother.id, mother.name, mother.zoneId, ME_DOCTOR, advice.length);
            setSteps(advice);
            doctorReview.markReviewed(mother);
          },
        }
      : !reviewed
        ? {
            label: 'Mark as reviewed',
            context: report ? `Report by ASHA ${report.asha} · ${report.when}` : undefined,
            enabled: true,
            haptic: 'success' as const,
            icon: 'checkCircle' as const,
            onPress: () => doctorReview.markReviewed(mother),
          }
        : {
            label: 'Reviewed',
            context: `You reviewed this report ${fmtWhen(reviewed.at)}`,
            enabled: false,
            icon: 'checkCircle' as const,
            onPress: () => {},
          };

  return (
    <Screen
      band
      stickyAccessory
      jumpRef={jump}
      eyebrow={`${mother.weeksLabel} · ${mother.village}`}
      title={mother.name}
      compactTitle={mother.name}
      compactAccessory={
        <View style={[styles.pill, { backgroundColor: tone.bar }]}>
          <AppText variant="overline" color={Colors.onCherry}>
            {tone.label.toUpperCase()}
          </AppText>
        </View>
      }
      back={{ label: 'Back', onPress: () => goBack('/doctor') }}
      accessory={<JumpBar fit initial={section === 'report' ? 'report' : undefined} items={SECTIONS} onJump={(key) => jump.current?.(offsets[key] ?? 0)} />}
      sheetProgress={sheet}
      cta={cta}
      overlay={
        <BottomSheet open={!!row} onClose={closeRow} progress={sheet} title={row ? `${row.id.toUpperCase()} · ${row.section}` : ''} closeLabel="Close">
          {row && (
            <>
              <View style={[styles.sheetBlock, mother.answers[row.id]?.danger && styles.sheetFlagged]}>
                <AppText variant="caption" color={Colors.inkMuted}>
                  Answer
                </AppText>
                <AppText variant="bodyStrong" color={mother.answers[row.id]?.danger ? Colors.cherry : Colors.ink}>
                  {mother.answers[row.id]?.value ?? 'Not asked'}
                </AppText>
              </View>
              <AppText variant="overline" color={Colors.inkMuted}>
                QUESTION AS ASKED
              </AppText>
              <View style={styles.sheetBlock}>
                <AppText variant="body">{row.questionKn}</AppText>
                <AppText variant="body">{row.questionHi}</AppText>
                <AppText variant="caption" color={Colors.inkMuted}>
                  {row.en}
                </AppText>
              </View>
              {row.rule && (
                <>
                  <AppText variant="overline" color={Colors.inkMuted}>
                    LINKED RULE
                  </AppText>
                  <View style={styles.sheetBlock}>
                    <AppText style={styles.mono}>{`RULE #${row.rule}  ${RULES[row.rule].logic}`}</AppText>
                    <AppText variant="caption" color={Colors.inkMuted}>
                      {`${RULES[row.rule].title}. ${RULES[row.rule].action}`}
                    </AppText>
                  </View>
                </>
              )}
            </>
          )}
        </BottomSheet>
      }
    >
      {/* 1 Summary */}
      <Animated.View entering={rise(0)} style={styles.section} {...anchor('summary')}>
        <View style={[styles.banner, { borderColor: tone.bar }]}>
          <View style={[styles.bannerBar, { backgroundColor: tone.bar }]} />
          <View style={styles.bannerBody}>
            <AppText variant="overline" color={tone.fg}>
              {banner.over}
            </AppText>
            <AppText variant="heading">{banner.title}</AppText>
            <AppText variant="caption" color={Colors.ink}>
              {banner.line}
            </AppText>
            {reviewed && (
              <View style={styles.reviewed}>
                <Icon name="checkCircle" size={16} color={Colors.sage} weight="fill" />
                <AppText variant="caption" color={Colors.sage} style={styles.flex}>
                  {`Reviewed ${fmtWhen(reviewed.at)}`}
                </AppText>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Undo reviewed"
                  hitSlop={12}
                  onPress={() => {
                    Haptic.light();
                    doctorReview.undoReviewed(mother.id);
                  }}
                >
                  <AppText variant="label" color={Colors.cherry}>
                    Undo
                  </AppText>
                </Pressable>
              </View>
            )}
          </View>
        </View>

        {!!mother.escalation && (
          <View style={styles.ashaNote} accessibilityLabel={`ASHA's note: ${mother.escalation.note}`}>
            <AppText variant="overline" color={Colors.cherry}>
              {`NEEDS CARE · ASHA ${(staff(mother.escalation.by)?.name ?? mother.asha).toUpperCase()} WROTE`}
            </AppText>
            <AppText variant="bodyStrong">{`“${mother.escalation.note}”`}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {fmtWhen(mother.escalation.at)}
            </AppText>
          </View>
        )}

        <View style={styles.facts}>
          <View style={styles.factRow}>
            <Fact label="Gestation" value={mother.weeksLabel} note={`${mother.months} months`} />
            <Fact
              label="Rules fired"
              value={String(codes.length)}
              note={codes.length ? `${flagged.length} danger sign${flagged.length === 1 ? '' : 's'}` : 'None'}
              alert={high}
            />
          </View>
          <View style={styles.factRow}>
            <Fact label="Last check-in" value={mother.lastCheck} small />
            <Fact label="ASHA" value={mother.asha} note={mother.village} small />
          </View>
        </View>

        {flagged.length > 0 && (
          <View style={styles.signs} accessibilityLabel={`Danger signs: ${flagged.map((q) => mother.answers[q.id]?.value).join('; ')}`}>
            <AppText variant="overline" color={Colors.inkMuted}>
              DANGER SIGNS REPORTED
            </AppText>
            {flagged.map((q) => (
              <View key={q.id} style={styles.sign}>
                <View style={styles.dot} />
                <AppText variant="body" style={styles.flex}>
                  {mother.answers[q.id]?.value}
                </AppText>
              </View>
            ))}
          </View>
        )}

        {codes.length > 0 && (
          <View style={styles.fold}>
            <Pressable
              accessibilityRole="button"
              accessibilityState={{ expanded: whyOpen }}
              onPress={() => {
                Haptic.light();
                setWhyOpen((o) => !o);
              }}
              style={({ pressed }) => [styles.foldHead, pressed && styles.pressed]}
            >
              <Icon name="shield" size={20} color={Colors.ink} />
              <AppText variant="bodyStrong" style={styles.flex}>
                Why this risk
              </AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                {`${codes.length} rule${codes.length === 1 ? '' : 's'}`}
              </AppText>
              <View style={whyOpen && styles.flip}>
                <Icon name="down" size={18} color={Colors.inkMuted} weight="bold" />
              </View>
            </Pressable>
            {whyOpen && <RuleAudit rules={codes} answers={mother.answers} />}
          </View>
        )}
      </Animated.View>

      {/* 2 Vitals */}
      <Animated.View entering={rise(1)} style={styles.section} {...anchor('vitals')}>
        <SectionTitle title="Vitals" note={`${mother.vitals.sys.length} visit${mother.vitals.sys.length === 1 ? '' : 's'}`} />
        <DoctorVitals vitals={mother.vitals} />
      </Animated.View>

      {/* 3 The ASHA's report */}
      <Animated.View entering={rise(2)} style={styles.section} {...anchor('report')}>
        <SectionTitle title="ASHA report" note={report ? report.when : 'None yet'} />
        {report ? (
          <>
            <View style={styles.reportHead}>
              <AppText variant="bodyStrong">{`Sakhi check-in by ASHA ${report.asha}`}</AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                {`${Object.keys(report.answers).length} answers · ${report.rules.length} rule${report.rules.length === 1 ? '' : 's'} fired · ${mother.reports.length} report${mother.reports.length === 1 ? '' : 's'} on file`}
              </AppText>
            </View>
            <TranscriptCard lines={report.transcript} preview={4} />
            <PdfButton mother={mother} report={report} />
          </>
        ) : (
          <AppText variant="body" color={Colors.inkMuted}>
            No voice check-in yet. It appears here as soon as the ASHA finishes one.
          </AppText>
        )}
      </Animated.View>

      {/* 4 Plan: advice + consultation */}
      <Animated.View entering={rise(3)} style={styles.section} {...anchor('plan')}>
        <SectionTitle title="Plan" note={mother.referred ? 'Referral sent' : 'Advice and consultation'} />
        <AdviceEditor mother={mother} steps={steps} onChange={setSteps} />
        <Consultation mother={mother} />
      </Animated.View>

      {/* 5 All answers */}
      <Animated.View entering={rise(4)} style={styles.fold} {...anchor('answers')}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded: allOpen }}
          onPress={() => {
            Haptic.light();
            setAllOpen((o) => !o);
          }}
          style={({ pressed }) => [styles.foldHead, pressed && styles.pressed]}
        >
          <Icon name="checklist" size={20} color={Colors.ink} />
          <AppText variant="bodyStrong" style={styles.flex}>
            {`All ${ANTENATAL_QUESTIONS.length} answers`}
          </AppText>
          <AppText variant="caption" color={flagged.length ? Colors.cherry : Colors.inkMuted}>
            {`${flagged.length} flagged`}
          </AppText>
          <View style={allOpen && styles.flip}>
            <Icon name="down" size={18} color={Colors.inkMuted} weight="bold" />
          </View>
        </Pressable>
        {allOpen &&
          ANTENATAL_QUESTIONS.map((q) => (
            <AnswerRow key={q.id} q={q} value={mother.answers[q.id]?.value ?? 'Not asked'} flagged={!!mother.answers[q.id]?.danger} onOpen={setRow} />
          ))}
      </Animated.View>
    </Screen>
  );
}

function Fact({ label, value, note, alert, small }: { label: string; value: string; note?: string; alert?: boolean; small?: boolean }) {
  return (
    <View style={styles.fact} accessible accessibilityLabel={`${label}: ${value}${note ? `, ${note}` : ''}`}>
      <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
        {label}
      </AppText>
      <AppText variant={small ? 'bodyStrong' : 'metric'} color={alert ? Colors.cherry : Colors.ink} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.75}>
        {value}
      </AppText>
      {!!note && (
        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
          {note}
        </AppText>
      )}
    </View>
  );
}

function SectionTitle({ title, note }: { title: string; note: string }) {
  return (
    <View style={styles.sectionTitle}>
      <AppText variant="heading" accessibilityRole="header" style={styles.flex}>
        {title}
      </AppText>
      <AppText variant="caption" color={Colors.inkMuted}>
        {note}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.6 },
  flip: { transform: [{ rotate: '180deg' }] },
  section: { gap: 12 },
  sectionTitle: { flexDirection: 'row', alignItems: 'baseline', gap: 8, paddingTop: 12, borderTopWidth: 1, borderTopColor: Colors.hairline },
  pill: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  banner: { flexDirection: 'row', borderRadius: DOC.radius, borderWidth: 1, backgroundColor: Colors.surface, overflow: 'hidden' },
  bannerBar: { width: 5 },
  bannerBody: { flex: 1, minWidth: 0, gap: 4, padding: 14 },
  reviewed: { flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 6, paddingTop: 8, borderTopWidth: 1, borderTopColor: Colors.hairline },
  facts: { gap: 10 },
  factRow: { flexDirection: 'row', gap: 10 },
  fact: {
    flex: 1,
    flexBasis: 0,
    minWidth: 0,
    minHeight: 106, // one height for all four, whatever each holds
    gap: 2,
    padding: 12,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
  },
  signs: { gap: 8, padding: 14, borderRadius: DOC.radius, backgroundColor: '#FBEAEE' },
  sign: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 9, backgroundColor: Colors.cherry },
  fold: { borderRadius: DOC.radius, borderWidth: 1, borderColor: DOC.border, backgroundColor: Colors.surface, overflow: 'hidden' },
  foldHead: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14 },
  reportHead: { gap: 2 },
  ashaNote: { gap: 4, padding: 14, borderRadius: DOC.radius, borderLeftWidth: 4, borderLeftColor: Colors.cherry, backgroundColor: '#FBEAEE' },
  answer: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 14, paddingVertical: 10, borderTopWidth: 1, borderTopColor: Colors.hairline },
  answerFlag: { backgroundColor: '#FBEAEE', borderLeftWidth: 4, borderLeftColor: Colors.cherry, paddingLeft: 10 },
  sheetBlock: { gap: 6, padding: 14, borderRadius: 10, backgroundColor: Colors.shell },
  sheetFlagged: { backgroundColor: '#FBEAEE' },
  mono: { fontFamily: MONO, fontSize: 13, lineHeight: 19, color: Colors.ink },
});
