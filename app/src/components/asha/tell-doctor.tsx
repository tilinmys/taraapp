import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import type { SharedValue } from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { PressButton } from '@/components/ui/press-button';
import { TextField } from '@/components/ui/text-field';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, network } from '@/data/care-network';
import { store, type MotherRecord } from '@/data/store';
import { useT } from '@/features/onboarding/context';
import type { StringKey } from '@/i18n/strings';

// The popup that appears ONLY after the ASHA marks a woman "Needs care".
//   1 Write    "What happened?" is REQUIRED: the doctor needs the why, not
//              just a red flag. One-tap sign chips add the common words, so
//              she rarely has to type much.
//   2 Send     every doctor of her PHC gets her name, "Needs care" and the
//              note (the bell on the doctor's home; the case shows it too)
//   3 Sent     the sheet says who was told and what they see; Done closes
// It closes ONLY through its own buttons: "Not now" (she stays marked, no
// one is told) or Done after sending. No x, no tap-outside, no Android back:
// a half-written escalation is never lost by a stray tap.

const SIGNS: StringKey[] = ['signBleeding', 'signHeadache', 'signBp', 'signFits', 'signPain', 'signFever', 'signMovement', 'signSwelling'];

export function TellDoctorSheet({ mother, progress, onClose }: { mother: MotherRecord | null; progress: SharedValue<number>; onClose: (told: string[] | null) => void }) {
  const t = useT();
  const [note, setNote] = useState('');
  const [tried, setTried] = useState(false);
  const [sent, setSent] = useState<string[] | null>(null);
  // Every opening is a fresh form.
  const [shownFor, setShownFor] = useState(mother);
  if (mother !== shownFor) {
    setShownFor(mother);
    if (mother) {
      setNote('');
      setTried(false);
      setSent(null);
    }
  }

  const ok = note.trim().length >= 3;
  const addSign = (key: StringKey) => {
    Haptic.light();
    const word = t(key);
    setNote((n) => (n.toLowerCase().includes(word.toLowerCase()) ? n : n.trim() ? `${n.trim()}, ${word}` : word));
  };
  const send = () => {
    setTried(true);
    if (!mother || !ok) {
      Haptic.warning();
      return;
    }
    store.setEscalation(mother.id, note, ME_ASHA);
    const names = network.alertDoctors(mother.id, mother.name, ME_ASHA, mother.zoneId, note);
    Haptic.success();
    setSent(names);
  };

  return (
    <BottomSheet
      open={!!mother}
      onClose={() => onClose(sent)}
      progress={progress}
      title={sent ? t('tellDoctorSent', { names: sent.join(', ') }) : t('tellDoctorTitle')}
      closeLabel={t('tellDoctorNo')}
      dismissible={false}
      footer={
        sent ? (
          <PressButton label={t('tellDoctorDone')} haptic="light" onPress={() => onClose(sent)} />
        ) : (
          <>
            <PressButton label={t('tellDoctorYes')} haptic="medium" icon={<Icon name="send" size={20} color={Colors.onCherry} />} onPress={send} />
            <PressButton label={t('tellDoctorNo')} variant="link" onPress={() => onClose(null)} />
          </>
        )
      }
    >
      {mother && sent && (
        <View style={styles.sent} accessibilityLiveRegion="polite">
          <Icon name="checkCircle" size={28} color={Colors.sage} weight="fill" />
          <View style={styles.flex}>
            <AppText variant="bodyStrong" color={Colors.sage}>
              {t('tellDoctorSentBody', { name: mother.name })}
            </AppText>
            <AppText variant="body" color={Colors.ink}>
              {`“${note.trim()}”`}
            </AppText>
          </View>
        </View>
      )}
      {mother && !sent && (
        <>
          <AppText variant="body" color={Colors.inkMuted}>
            {t('tellDoctorBody', { name: mother.name })}
          </AppText>
          <TextField
            label={t('tellDoctorWhat')}
            value={note}
            onChangeText={setNote}
            multiline
            maxLength={400}
            placeholder={t('tellDoctorWhatHint')}
            error={tried && !ok ? t('tellDoctorWhatError') : undefined}
            style={styles.note}
          />
          <View style={styles.quick}>
            <AppText variant="overline" color={Colors.inkMuted}>
              {t('tellDoctorQuick').toLocaleUpperCase()}
            </AppText>
            <View style={styles.chips}>
              {SIGNS.map((k) => (
                <Pressable key={k} accessibilityRole="button" accessibilityLabel={t(k)} onPress={() => addSign(k)} style={({ pressed }) => [styles.chip, pressed && styles.pressed]}>
                  <Icon name="plus" size={14} color={Colors.cherry} weight="bold" />
                  <AppText variant="caption" color={Colors.ink}>
                    {t(k)}
                  </AppText>
                </Pressable>
              ))}
            </View>
          </View>
        </>
      )}
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0, gap: 6 },
  pressed: { opacity: 0.7 },
  note: { minHeight: 96, paddingTop: 14, textAlignVertical: 'top' },
  quick: { gap: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 40, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.blush, backgroundColor: Colors.surface },
  sent: { flexDirection: 'row', alignItems: 'flex-start', gap: 12, padding: 14, borderRadius: 16, backgroundColor: Colors.sageTint },
});
