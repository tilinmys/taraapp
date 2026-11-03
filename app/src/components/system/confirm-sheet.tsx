import { StyleSheet, View } from 'react-native';
import type { SharedValue } from 'react-native-reanimated';

import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { PressButton } from '@/components/ui/press-button';
import { Colors } from '@/constants/theme';

// Confirmation for actions that leave or end something (exit a check-in,
// sign out). Never a hard-wall alert: one clear primary choice, one quiet
// way back, and a sentence saying what happens to the user's work.

type Props = {
  open: boolean;
  progress: SharedValue<number>;
  title: string;
  body: string;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
};

export function ConfirmSheet({ open, progress, title, body, confirmLabel, cancelLabel = 'Stay here', onConfirm, onCancel }: Props) {
  return (
    <BottomSheet open={open} onClose={onCancel} progress={progress} title={title} closeLabel={cancelLabel}>
      <View style={styles.body}>
        <AppText variant="body" color={Colors.inkMuted}>
          {body}
        </AppText>
        <PressButton label={confirmLabel} haptic="medium" onPress={onConfirm} />
        <PressButton label={cancelLabel} variant="link" onPress={onCancel} />
      </View>
    </BottomSheet>
  );
}

const styles = StyleSheet.create({
  body: { gap: 14, paddingBottom: 4 },
});
