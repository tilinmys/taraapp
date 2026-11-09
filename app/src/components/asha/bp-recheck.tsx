import { Pressable, StyleSheet, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA } from '@/data/care-network';
import { store, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { bpState, pendingRecheck, referNow } from '@/lib/bp';
import { whenLabel } from '@/lib/day-label';

// The BP action that sits in the TOP-RIGHT of a woman's card (her page's
// Talk with Sakhi card, today's visit list), for ANY BP out of range:
//   not set              "Recheck in 6 h"   tap: saved with her record
//                        (filled red when severe / with danger signs: she
//                        is referred NOW as well; the card next to it says so)
//   set                  "✓ Recheck today, 18:40"
//   due time passed      "Recheck now"      (cherry, filled)
// Nothing when her BP is normal, there is no reading, or the ASHA marked her
// Well / Needs care in How is she? (her call decides, see condition-picker).

export function BpRecheckPill({ mother }: { mother: MotherRecord }) {
  const t = useT();
  const { language } = useOnboarding();
  const bp = bpState(mother);
  // The ASHA's own call decides: marked Well, or escalated as Needs care,
  // there is no recheck to offer (How is she? on her page changes it).
  if (!bp || mother.ashaFlag === 'well' || mother.ashaFlag === 'needs_care') return null;
  const reading = `${bp.sys}/${bp.dia} mmHg`;

  const recheck = pendingRecheck(mother);
  if (recheck?.overdue) {
    return (
      <View style={[styles.pill, styles.urgent]} accessible accessibilityLabel={`${t('recheckNowShort')}: ${reading}`}>
        <Icon name="timer" size={14} color={Colors.onCherry} />
        <AppText variant="label" color={Colors.onCherry} numberOfLines={1} style={styles.shrink}>
          {t('recheckNowShort')}
        </AppText>
      </View>
    );
  }
  if (recheck) {
    const when = whenLabel(recheck.dueAt, language);
    const line = t('recheckAtShort', { when });
    return (
      <View style={[styles.pill, styles.set]} accessible accessibilityLabel={t('recheckDueShort', { when })}>
        <Icon name="checkCircle" size={14} color={Colors.sage} weight="fill" />
        <AppText variant="label" color={Colors.sage} numberOfLines={1} style={styles.shrink}>
          {line}
        </AppText>
      </View>
    );
  }
  const urgent = referNow(bp);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${t('recheck6Hours')}: ${reading}${urgent ? `. ${t('referNowShort')}` : ''}`}
      hitSlop={6}
      onPress={() => {
        Haptic.medium();
        store.scheduleBpRecheck(mother.id, { by: ME_ASHA, bp: `${bp.sys}/${bp.dia}` });
      }}
      style={({ pressed }) => [styles.pill, urgent ? styles.urgent : styles.action, pressed && styles.pressed]}
    >
      <Icon name="timer" size={14} color={urgent ? Colors.onCherry : Colors.cherry} />
      <AppText variant="label" color={urgent ? Colors.onCherry : Colors.cherry} numberOfLines={1} style={styles.shrink}>
        {t('recheckShort')}
      </AppText>
    </Pressable>
  );
}

/**
 * The strip across the TOP of a woman's card (today's visits, her village
 * list) when her BP is high or low: the reading on the left, the recheck
 * pill top-right. Nothing when her BP is normal. `inset`: the card's padding
 * (the strip runs edge to edge), `radius`: the card's inner corner radius.
 */
export function BpStrip({ mother, inset = 14, radius = 15 }: { mother: MotherRecord; inset?: number; radius?: number }) {
  const t = useT();
  const bp = bpState(mother);
  if (!bp) return null;
  const low = bp.level === 'low';
  const word = t(referNow(bp) ? 'referNowShort' : low ? 'bpLowShort' : 'bpHighShort');
  const tone = low ? Colors.pumpkinInk : Colors.cherry;
  return (
    <View style={[styles.strip, { marginHorizontal: -inset, marginTop: -inset, paddingHorizontal: inset, borderTopLeftRadius: radius, borderTopRightRadius: radius }, low && styles.stripLow]}>
      <View style={styles.read} accessible accessibilityLabel={`${word} ${bp.sys}/${bp.dia} mmHg`}>
        <Icon name="warning" size={16} color={tone} weight="fill" />
        <AppText variant="label" color={tone} numberOfLines={1} style={styles.shrink}>
          {`${bp.sys}/${bp.dia} · ${word}`}
        </AppText>
      </View>
      <View style={styles.act}>
        <BpRecheckPill mother={mother} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  // One row: the reading gives way first on a narrow phone, never the pill.
  strip: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, paddingVertical: 8, backgroundColor: Colors.cherryMist },
  stripLow: { backgroundColor: Colors.orangeTint },
  act: { flexShrink: 0, maxWidth: '70%' },
  read: { flexShrink: 1, minWidth: 0, flexDirection: 'row', alignItems: 'center', gap: 6 },
  shrink: { flexShrink: 1 },
  pressed: { transform: [{ scale: 0.96 }] },
  // flexShrink: a long Kannada label narrows the pill, never the card.
  pill: { flexShrink: 1, maxWidth: '100%', minHeight: 34, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, borderRadius: 17, borderWidth: 1.5 },
  action: { borderColor: Colors.cherry, backgroundColor: Colors.surface },
  set: { borderColor: Colors.sage, backgroundColor: Colors.surface },
  // A light edge keeps the filled pill visible on the cherry Sakhi card too.
  urgent: { borderColor: Colors.surface, backgroundColor: Colors.cherry },
});
