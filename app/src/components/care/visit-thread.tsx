import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor } from '@/constants/typography';
import { fmtTime, fmtWhen, network, staff, useNetwork } from '@/data/care-network';

// The ASHA <-> doctor thread on one booked visit, shown in a bottom sheet on
// both sides. Chat bubbles (mine on the right, theirs on the left), newest
// at the bottom, and one text box with a send button. The other side gets a
// notification (network.addComment).

export function VisitThread({ bookingId, me, myId, motherName }: { bookingId: string; me: 'asha' | 'doctor'; myId: string; motherName: string }) {
  const net = useNetwork();
  const [text, setText] = useState('');
  const b = net.bookings.find((x) => x.id === bookingId);
  const s = b && net.slots.find((x) => x.id === b.slotId);
  if (!b || !s) return null;
  const other = me === 'asha' ? staff(s.doctorId)?.name : staff(b.ashaId)?.name;
  const send = () => {
    if (!text.trim()) return;
    network.addComment(bookingId, me, myId, text, motherName);
    Haptic.success();
    setText('');
  };

  return (
    <View style={styles.wrap}>
      <AppText variant="caption" color={Colors.inkMuted}>
        {`${motherName} · ${staff(s.doctorId)?.name ?? ''} · ${fmtWhen(s.start)}`}
      </AppText>
      <View style={styles.thread}>
        {b.comments.length === 0 && (
          <AppText variant="body" color={Colors.inkMuted}>
            {`No messages yet. Write to ${other ?? 'them'} about this visit.`}
          </AppText>
        )}
        {b.comments.map((c) => {
          const mine = c.from === me;
          return (
            <View key={c.id} style={[styles.row, mine && styles.rowMine]}>
              <View style={[styles.bubble, mine ? styles.mine : styles.theirs]}>
                <AppText variant="label" color={mine ? Colors.cherryPressed : Colors.inkMuted}>
                  {mine ? 'You' : (staff(c.byId)?.name ?? '')}
                </AppText>
                <AppText variant="body">{c.text}</AppText>
                <AppText variant="caption" color={Colors.inkMuted}>
                  {fmtTime(c.at)}
                </AppText>
              </View>
            </View>
          );
        })}
      </View>
      <View style={styles.composer}>
        <TextInput
          value={text}
          onChangeText={setText}
          placeholder={`Message to ${other ?? ''}`}
          placeholderTextColor={Colors.inkSubtle}
          selectionColor={Colors.cherry}
          multiline
          maxLength={300}
          accessibilityLabel={`Message to ${other ?? ''}`}
          style={[styles.input, fontFor('input')]}
          maxFontSizeMultiplier={1.4}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Send"
          disabled={!text.trim()}
          onPress={send}
          style={({ pressed }) => [styles.send, !text.trim() && styles.disabled, pressed && styles.pressed]}
        >
          <Icon name="send" size={20} color={Colors.onCherry} weight="fill" />
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 12, paddingBottom: 8 },
  thread: { gap: 8 },
  row: { flexDirection: 'row' },
  rowMine: { justifyContent: 'flex-end' },
  bubble: { maxWidth: '85%', gap: 2, paddingHorizontal: 12, paddingVertical: 8, borderRadius: 16 },
  mine: { backgroundColor: Colors.cherryMist, borderBottomRightRadius: 4 },
  theirs: { backgroundColor: Colors.shell, borderBottomLeftRadius: 4 },
  composer: { flexDirection: 'row', alignItems: 'flex-end', gap: 8 },
  input: {
    flex: 1,
    minHeight: 50,
    maxHeight: 120,
    paddingHorizontal: 16,
    paddingVertical: 12,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: Colors.outline,
    color: Colors.ink,
    backgroundColor: Colors.surface,
  },
  send: { width: 50, height: 50, borderRadius: 25, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  disabled: { opacity: 0.4 },
  pressed: { opacity: 0.7 },
});
