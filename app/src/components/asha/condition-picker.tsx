import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeOut, useReducedMotion } from 'react-native-reanimated';

import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { careStatus, store, type AshaFlag, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { bpState, pendingRecheck } from '@/lib/bp';
import { whenLabel } from '@/lib/day-label';

// "How is she?": the ASHA's own call, ONE choice of three:
//   Well             she is fine (a pending recheck is cancelled)
//   Recheck in 6 h   measure her BP again in 6 hours (set here, by hand)
//   Needs care       escalate (the screen then asks whether to tell the doctor)
// Closed, it is a pill with the current call. With no call yet and her BP
// out of range, Recheck is marked "Suggested" and a line says why; once she
// is marked Well, nothing about the recheck shows any more. A recheck that
// is due turns into "Recheck now" with a button to enter her BP; the new
// reading clears the call (store.saveClinical), so the ASHA decides again
// on the new numbers.

type Tone = { fg: string; bg: string };
const TONE: Record<'well' | 'recheck' | 'due' | 'care' | 'none', Tone> = {
  well: { fg: Colors.sage, bg: Colors.sageTint },
  recheck: { fg: Colors.pumpkinInk, bg: Colors.orangeTint },
  due: { fg: Colors.cherry, bg: Colors.cherryMist },
  care: { fg: Colors.cherry, bg: Colors.cherryMist },
  none: { fg: Colors.plum, bg: Colors.lilacTint },
};

export function ConditionPicker({ mother, onNeedsCare }: { mother: MotherRecord; onNeedsCare: (m: MotherRecord) => void }) {
  const t = useT();
  const { language } = useOnboarding();
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const flag = mother.ashaFlag;
  const bp = bpState(mother);
  const recheck = pendingRecheck(mother);
  const reading = bp ? `${bp.sys}/${bp.dia}` : '';

  // What the closed pill says: her call, else what her findings say.
  const c = careStatus(mother);
  const shown =
    flag === 'recheck' && recheck
      ? recheck.overdue
        ? { tone: TONE.due, label: t('recheckNowShort'), icon: 'timer' as IconName }
        : { tone: TONE.recheck, label: t('recheckAtShort', { when: whenLabel(recheck.dueAt, language) }), icon: 'timer' as IconName }
      : flag === 'well' || (!flag && c === 'ok')
        ? { tone: TONE.well, label: t('statusWell'), icon: null }
        : flag === 'needs_care' || (!flag && c === 'alert')
          ? { tone: TONE.care, label: t('statusNeedsCare'), icon: null }
          : { tone: TONE.none, label: t('notCheckedYet'), icon: null };

  const pick = (next: AshaFlag) => {
    setOpen(false);
    if (next === flag) return;
    store.setFlag(mother.id, next);
    if (next === 'needs_care') {
      Haptic.warning();
      onNeedsCare(mother);
    } else Haptic.success();
  };

  const options: { flag: AshaFlag; label: string; sub?: string; tone: Tone; suggested?: boolean }[] = [
    { flag: 'well', label: t('statusWell'), tone: TONE.well },
    {
      flag: 'recheck',
      label: t('recheck6Hours'),
      sub: bp ? t(bp.level === 'low' ? 'howRecheckLow' : 'howRecheckHigh', { bp: reading }) : t('howRecheckSub'),
      tone: TONE.recheck,
      suggested: !flag && !!bp,
    },
    { flag: 'needs_care', label: t('statusNeedsCare'), tone: TONE.care },
  ];

  return (
    <View style={styles.wrap}>
      <View style={styles.head}>
        <AppText variant="caption" color={Colors.inkMuted} style={styles.flex}>
          {t('condition')}
        </AppText>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${t('condition')} ${shown.label}`}
          accessibilityState={{ expanded: open }}
          onPress={() => {
            Haptic.light();
            setOpen((o) => !o);
          }}
          style={({ pressed }) => [styles.pill, { backgroundColor: shown.tone.bg, borderColor: shown.tone.fg }, pressed && styles.pressed]}
        >
          {shown.icon ? <Icon name={shown.icon} size={14} color={shown.tone.fg} /> : <View style={[styles.dot, { backgroundColor: shown.tone.fg }]} />}
          <AppText variant="label" color={shown.tone.fg} numberOfLines={1} style={styles.shrink}>
            {shown.label}
          </AppText>
          <Icon name="down" size={16} color={shown.tone.fg} weight="bold" />
        </Pressable>
      </View>

      {/* No call yet and her BP is out of range: say why a recheck is suggested. */}
      {!flag && !!bp && !open && (
        <View style={styles.hint} accessibilityLiveRegion="polite">
          <Icon name="warning" size={16} color={Colors.pumpkinInk} weight="fill" />
          <AppText variant="caption" color={Colors.pumpkinInk} style={styles.flex}>
            {t(bp.level === 'low' ? 'howSuggestLow' : 'howSuggestHigh', { bp: reading })}
          </AppText>
        </View>
      )}

      {/* A recheck that is due: the next step is her new BP. */}
      {flag === 'recheck' && recheck?.overdue && !open && (
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            Haptic.light();
            router.push({ pathname: '/asha/clinical/[id]', params: { id: mother.id } });
          }}
          style={({ pressed }) => [styles.dueBtn, pressed && styles.pressed]}
        >
          <Icon name="pulse" size={18} color={Colors.onCherry} />
          <AppText variant="buttonSmall" color={Colors.onCherry} numberOfLines={1} style={styles.shrink}>
            {t('howEnterBp')}
          </AppText>
        </Pressable>
      )}

      {open && (
        <Animated.View entering={reduceMotion ? undefined : FadeIn.duration(140)} exiting={reduceMotion ? undefined : FadeOut.duration(100)} style={styles.menu} accessibilityRole="radiogroup">
          {options.map((o) => {
            const on = o.flag === flag;
            return (
              <Pressable
                key={o.flag}
                accessibilityRole="radio"
                accessibilityState={{ checked: on }}
                accessibilityLabel={[o.label, o.sub, o.suggested ? t('howSuggested') : ''].filter(Boolean).join('. ')}
                onPress={() => pick(o.flag)}
                style={({ pressed }) => [styles.option, { backgroundColor: o.tone.bg }, on && { borderColor: o.tone.fg }, pressed && styles.pressed]}
              >
                <View style={[styles.bigDot, { backgroundColor: o.tone.fg }]} />
                <View style={styles.flex}>
                  <AppText variant="bodyStrong" color={o.tone.fg}>
                    {o.label}
                  </AppText>
                  {!!o.sub && (
                    <AppText variant="caption" color={Colors.inkMuted}>
                      {o.sub}
                    </AppText>
                  )}
                </View>
                {o.suggested && !on && (
                  <View style={[styles.tag, { borderColor: o.tone.fg }]}>
                    <AppText variant="label" color={o.tone.fg} numberOfLines={1}>
                      {t('howSuggested')}
                    </AppText>
                  </View>
                )}
                {on && <Icon name="check" size={18} color={o.tone.fg} weight="bold" />}
              </Pressable>
            );
          })}
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  shrink: { flexShrink: 1 },
  pressed: { opacity: 0.75 },
  wrap: { gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pill: { minHeight: 40, maxWidth: '70%', flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, borderRadius: 20, borderWidth: 1.5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  hint: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, padding: 10, borderRadius: 12, backgroundColor: Colors.orangeTint },
  dueBtn: { minHeight: 44, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 14, borderRadius: 22, backgroundColor: Colors.cherry },
  menu: { gap: 8 },
  option: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 14, borderWidth: 2, borderColor: 'transparent' },
  bigDot: { width: 14, height: 14, borderRadius: 7 },
  tag: { flexShrink: 0, paddingHorizontal: 8, paddingVertical: 2, borderRadius: 10, borderWidth: 1.5 },
});
