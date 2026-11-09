import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor, scriptForLang } from '@/constants/typography';
import { ME_ASHA } from '@/data/care-network';
import { questionsFor, questionText, type AnswerRecord, type Question } from '@/data/mockScreening';
import { store, useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';

// After the voice check-in, BEFORE the report: the ASHA checks what Sakhi
// wrote down and corrects it, or fills in what was missed (the framework's
// "allow ASHA verification"). One row per question of the visit, in order;
// the ones that need a look (not clear, no answer, skipped, "check") are
// marked and counted at the top. Tap a row to edit: her words, and for a
// danger question, whether it is a danger sign. A corrected answer is saved
// with "corrected by" (who, when), so the doctor can tell it from Sakhi's.
// "Save and see report" goes on to the report (nothing to change is fine).

// "(part not answered: check)" is matched by "not answered"; "(check)" by itself.
const NEEDS_LOOK = /not clear|no answer|skipped|\(check\)|: check\)|not known|not answered|not sure/i;
const needsLook = (a?: AnswerRecord) => !a || NEEDS_LOOK.test(a.value);
/** Sakhi wrote something down but flagged it (partial / unclear): the amber "Review needed" badge. */
const flagged = (a?: AnswerRecord) => !!a && !a.corrected && NEEDS_LOOK.test(a.value);

export default function Review() {
  const { id, report } = useLocalSearchParams<{ id: string; report?: string }>();
  const mother = useMother(id);
  const t = useT();
  const { language } = useOnboarding();
  const [open, setOpen] = useState<string | null>(null);
  if (!mother) return null;
  const draft = mother.draft ?? {};
  const questions = questionsFor(mother.draftVisit ?? 1).filter((q) => q.id !== 'close');
  const toCheck = questions.filter((q) => needsLook(draft[q.id])).length;
  // Step 2: her measurements (BP, weight, Hb), then the report: the rules see both.
  const go = () => router.replace({ pathname: '/asha/clinical/[id]', params: { id: mother.id, report, fromReview: '1' } });

  return (
    <Screen
      eyebrow={mother.name}
      title={t('reviewTitle')}
      compactTitle={t('reviewTitle')}
      back={{ label: t('reviewSkip'), icon: 'close', onPress: go }}
      cta={{ label: t('clinNextEnter'), enabled: true, icon: 'forward', haptic: 'success', onPress: go }}
    >
      <AppText variant="body" color={Colors.inkMuted}>
        {t('reviewHint')}
      </AppText>
      <View style={[styles.summary, toCheck ? styles.summaryCheck : styles.summaryOk]} accessibilityLiveRegion="polite">
        <Icon name={toCheck ? 'warning' : 'checkCircle'} size={20} color={toCheck ? Colors.pumpkinInk : Colors.sage} weight="fill" />
        <AppText variant="bodyStrong" color={toCheck ? Colors.pumpkinInk : Colors.sage} style={styles.flex}>
          {toCheck ? t('reviewToCheck', { n: toCheck }) : t('reviewAllGood')}
        </AppText>
      </View>
      {questions.map((q, i) => (
        <Row
          key={q.id}
          n={i + 1}
          q={q}
          text={questionText(q, language)}
          answer={draft[q.id]}
          open={open === q.id}
          onOpen={() => setOpen(open === q.id ? null : q.id)}
          onSave={(rec) => {
            store.recordAnswer(mother.id, q.id, rec);
            Haptic.success();
            setOpen(null);
          }}
        />
      ))}
    </Screen>
  );
}

function Row({
  n,
  q,
  text,
  answer,
  open,
  onOpen,
  onSave,
}: {
  n: number;
  q: Question;
  text: string;
  answer?: AnswerRecord;
  open: boolean;
  onOpen: () => void;
  onSave: (r: AnswerRecord) => void;
}) {
  const t = useT();
  const { language } = useOnboarding();
  const look = needsLook(answer);
  const corrected = !!answer?.corrected;
  const [value, setValue] = useState(answer?.value ?? '');
  const [danger, setDanger] = useState<boolean>(!!answer?.danger);
  const save = () => {
    const v = value.trim();
    if (!v) return;
    const same = v === answer?.value;
    onSave({
      value: v,
      danger: q.dangerTrigger ? danger : false,
      // Her own words replace Sakhi's reading: the signs follow her "yes / no".
      ...(q.dangerTrigger && danger ? { signs: same ? answer?.signs : undefined } : {}),
      corrected: { by: ME_ASHA, at: Date.now() },
    });
  };
  return (
    <View style={[styles.row, look && styles.rowLook, answer?.danger && styles.rowDanger]}>
      <Pressable accessibilityRole="button" accessibilityLabel={`${flagged(answer) ? `${t('reviewNeeded')}. ` : ''}${text}. ${answer?.value ?? t('reviewNotAsked')}`} accessibilityState={{ expanded: open }} onPress={onOpen} style={({ pressed }) => [styles.rowHead, pressed && styles.pressed]}>
        <AppText variant="label" color={Colors.inkMuted} style={styles.num}>
          {`${n}`}
        </AppText>
        <View style={styles.flex}>
          {flagged(answer) && (
            <View style={styles.badge} accessible accessibilityLabel={t('reviewNeeded')}>
              <Icon name="warning" size={14} color={Colors.pumpkinInk} weight="fill" />
              <AppText variant="label" color={Colors.pumpkinInk} numberOfLines={1} style={styles.shrink}>
                {t('reviewNeeded')}
              </AppText>
            </View>
          )}
          <AppText variant="label">{text}</AppText>
          <AppText variant="body" color={answer?.danger ? Colors.cherry : look ? Colors.pumpkinInk : Colors.ink}>
            {answer?.value ?? t('reviewNotAsked')}
          </AppText>
          {corrected && (
            <AppText variant="caption" color={Colors.sage}>
              {t('reviewEdited')}
            </AppText>
          )}
        </View>
        <AppText variant="buttonSmall" color={Colors.cherry}>
          {open ? t('reviewCancel') : t('reviewEdit')}
        </AppText>
      </Pressable>
      {open && (
        <View style={styles.editor}>
          <TextInput
            value={value}
            onChangeText={setValue}
            multiline
            maxLength={200}
            accessibilityLabel={text}
            placeholderTextColor={Colors.inkSubtle}
            selectionColor={Colors.cherry}
            style={[styles.input, fontFor('input', scriptForLang(language))]}
            maxFontSizeMultiplier={1.4}
          />
          {q.dangerTrigger && (
            <View style={styles.dangerRow}>
              <AppText variant="label" style={styles.flex}>
                {t('reviewDanger')}
              </AppText>
              {[true, false].map((d) => (
                <Pressable
                  key={String(d)}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: danger === d }}
                  onPress={() => setDanger(d)}
                  style={({ pressed }) => [styles.pill, danger === d && (d ? styles.pillDanger : styles.pillOk), pressed && styles.pressed]}
                >
                  <AppText variant="buttonSmall" color={danger === d ? Colors.onCherry : Colors.ink}>
                    {d ? t('clinYes') : t('clinNo')}
                  </AppText>
                </Pressable>
              ))}
            </View>
          )}
          <Pressable
            accessibilityRole="button"
            disabled={!value.trim()}
            onPress={save}
            style={({ pressed }) => [styles.save, !value.trim() && styles.off, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={Colors.onCherry}>
              {t('reviewSave')}
            </AppText>
          </Pressable>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.75 },
  off: { opacity: 0.4 },
  summary: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: 16 },
  summaryCheck: { backgroundColor: Colors.orangeTint },
  summaryOk: { backgroundColor: Colors.sageTint },
  row: { borderRadius: 16, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface, overflow: 'hidden' },
  rowLook: { borderColor: Colors.pumpkin, borderWidth: 1.5 },
  rowDanger: { borderColor: Colors.cherry, borderWidth: 1.5 },
  rowHead: { flexDirection: 'row', alignItems: 'flex-start', gap: 10, padding: 12 },
  num: { minWidth: 20 },
  shrink: { flexShrink: 1 },
  badge: { alignSelf: 'flex-start', maxWidth: '100%', flexDirection: 'row', alignItems: 'center', gap: 4, marginBottom: 4, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 10, backgroundColor: Colors.orangeTint },
  editor: { gap: 10, paddingHorizontal: 12, paddingBottom: 12 },
  input: { minHeight: 64, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1.5, borderColor: Colors.outline, color: Colors.ink, backgroundColor: Colors.surface, textAlignVertical: 'top' },
  dangerRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pill: { minHeight: 40, minWidth: 64, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.bandLine },
  pillDanger: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  pillOk: { backgroundColor: Colors.sage, borderColor: Colors.sage },
  save: { minHeight: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 22, backgroundColor: Colors.cherry },
});
