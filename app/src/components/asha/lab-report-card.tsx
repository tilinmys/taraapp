import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import * as Sharing from 'expo-sharing';
import { useState } from 'react';
import { Image, Linking, Modal, Platform, Pressable, StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppIcon } from '@/components/system/app-icon';
import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA } from '@/data/care-network';
import { store, type LabUpload, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { whenLabel } from '@/lib/day-label';
import { keepLabFile } from '@/lib/lab-files';

// Her Lab report, ONE card (the last section of her page):
//   1. Take photo / Choose file: the report itself is the record, so nothing
//      is typed in. Saved at once, dated, and never overwritten.
//   2. Her report history underneath, newest first (the latest three, "Show
//      all" for the rest): tap one to see it (a photo), or open it (a PDF).
// No new section and no new screen for the upload: it all happens here.

const SHOWN = 3;

/** A new upload's id (made when the ASHA taps, never while rendering). */
const newId = () => `lab-${Date.now().toString(36)}`;

export function LabReportCard({ mother }: { mother: MotherRecord }) {
  const t = useT();
  const { language } = useOnboarding();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
  const [all, setAll] = useState(false);
  const [viewing, setViewing] = useState<LabUpload | null>(null);

  const uploads = mother.labReports ?? [];

  const upload = async (from: 'camera' | 'file') => {
    // Every upload is a "Lab report"; its date tells one from another.
    const label = t('labTitle');
    Haptic.light();
    setBusy(true);
    setNote(null);
    try {
      let picked: { uri: string; mime?: string | null; fileName?: string | null } | null = null;
      if (from === 'camera') {
        const perm = Platform.OS === 'web' ? { granted: true } : await ImagePicker.requestCameraPermissionsAsync();
        if (!perm.granted) {
          setNote({ text: t('labNoCamera'), ok: false });
          return;
        }
        const r = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.6 });
        if (!r.canceled && r.assets[0]) picked = { uri: r.assets[0].uri, mime: r.assets[0].mimeType, fileName: r.assets[0].fileName };
      } else {
        const r = await DocumentPicker.getDocumentAsync({ type: ['image/*', 'application/pdf'], copyToCacheDirectory: true });
        if (!r.canceled && r.assets[0]) picked = { uri: r.assets[0].uri, mime: r.assets[0].mimeType, fileName: r.assets[0].name };
      }
      if (!picked) return;
      const pdf = picked.mime === 'application/pdf' || /\.pdf$/i.test(picked.fileName ?? picked.uri);
      const image = !pdf && (picked.mime?.startsWith('image/') || /\.(jpe?g|png|heic|webp)$/i.test(picked.fileName ?? picked.uri) || from === 'camera');
      const ext = pdf ? 'pdf' : image ? (/\.png$/i.test(picked.fileName ?? '') ? 'png' : 'jpg') : (picked.fileName?.split('.').pop() ?? 'bin');
      const id = newId();
      const uri = await keepLabFile(id, picked.uri, ext);
      store.addLabReport(mother.id, { id, name: label, uri, kind: pdf ? 'pdf' : image ? 'image' : 'file', by: ME_ASHA });
      Haptic.success();
      setNote({ text: t('labSaved', { name: label }), ok: true });
      setAll(false);
    } catch {
      Haptic.warning();
      setNote({ text: t('labFailed'), ok: false });
    } finally {
      setBusy(false);
    }
  };

  const shown = all ? uploads : uploads.slice(0, SHOWN);

  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <AppIcon name="report" size={36} />
        <View style={styles.flex}>
          <AppText variant="heading">{t('labTitle')}</AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            {uploads.length ? t('labReportsN', { n: uploads.length, when: whenLabel(uploads[0].at, language) }) : t('labNoReports')}
          </AppText>
        </View>
      </View>

      <View style={styles.actions}>
        <Action icon="camera" label={t('labPhoto')} enabled={!busy} onPress={() => upload('camera')} />
        <Action icon="pdf" label={t('labFile')} enabled={!busy} onPress={() => upload('file')} />
      </View>

      {!!note && (
        <AppText variant="caption" color={note.ok ? Colors.sage : Colors.cherry} accessibilityLiveRegion="polite">
          {note.text}
        </AppText>
      )}

      {/* Her report history, newest first. */}
      {shown.map((u, i) => (
        <Pressable
          key={u.id}
          accessibilityRole="button"
          accessibilityLabel={`${u.name}, ${whenLabel(u.at, language)}`}
          onPress={() => {
            Haptic.light();
            setViewing(u);
          }}
          style={({ pressed }) => [styles.row, i > 0 && styles.line, pressed && styles.pressed]}
        >
          {u.kind === 'image' ? (
            <Image source={{ uri: u.uri }} style={styles.thumb} />
          ) : (
            <View style={[styles.thumb, styles.fileThumb]}>
              <Icon name="pdf" size={20} color={Colors.cherry} />
            </View>
          )}
          <View style={styles.flex}>
            <AppText variant="bodyStrong" numberOfLines={1}>
              {u.name}
            </AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {whenLabel(u.at, language)}
            </AppText>
          </View>
          <Icon name="forward" size={16} color={Colors.inkMuted} />
        </Pressable>
      ))}
      {uploads.length > SHOWN && (
        <Pressable accessibilityRole="button" onPress={() => setAll((a) => !a)} hitSlop={6} style={styles.more}>
          <AppText variant="buttonSmall" color={Colors.cherry}>
            {all ? t('labShowLess') : t('labShowAll', { n: uploads.length })}
          </AppText>
        </Pressable>
      )}

      <Viewer upload={viewing} onClose={() => setViewing(null)} onDelete={(u) => store.removeLabReport(mother.id, u.id)} />
    </View>
  );
}

function Action({ icon, label, enabled, onPress }: { icon: IconName; label: string; enabled: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !enabled }}
      disabled={!enabled}
      onPress={onPress}
      style={({ pressed }) => [styles.action, !enabled && styles.off, pressed && styles.pressed]}
    >
      <Icon name={icon} size={20} color={Colors.onCherry} />
      <AppText variant="buttonSmall" color={Colors.onCherry} numberOfLines={1} style={styles.shrink}>
        {label}
      </AppText>
    </Pressable>
  );
}

/** One report, full screen: the photo, or a button to open the file; Delete for a wrong upload. */
function Viewer({ upload, onClose, onDelete }: { upload: LabUpload | null; onClose: () => void; onDelete: (u: LabUpload) => void }) {
  const t = useT();
  const { language } = useOnboarding();
  const insets = useSafeAreaInsets();
  const openFile = async (u: LabUpload) => {
    if (Platform.OS === 'web') Linking.openURL(u.uri).catch(() => {});
    else if (await Sharing.isAvailableAsync()) Sharing.shareAsync(u.uri, { mimeType: u.kind === 'pdf' ? 'application/pdf' : undefined }).catch(() => {});
  };
  return (
    <Modal visible={!!upload} animationType="fade" onRequestClose={onClose} transparent>
      {!!upload && (
        <View style={[styles.viewer, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 12 }]}>
          <View style={styles.viewerHead}>
            <View style={styles.flex}>
              <AppText variant="heading" color={Colors.onCherry} numberOfLines={1}>
                {upload.name}
              </AppText>
              <AppText variant="caption" color={Colors.onCherry}>
                {whenLabel(upload.at, language)}
              </AppText>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel={t('labClose')} onPress={onClose} hitSlop={10} style={styles.viewerBtn}>
              <Icon name="close" size={22} color={Colors.onCherry} />
            </Pressable>
          </View>
          {upload.kind === 'image' ? (
            <Image source={{ uri: upload.uri }} style={styles.full} resizeMode="contain" accessibilityLabel={upload.name} />
          ) : (
            <View style={[styles.full, styles.fileView]}>
              <Icon name="pdf" size={56} color={Colors.onCherry} />
              <Pressable accessibilityRole="button" onPress={() => openFile(upload)} style={({ pressed }) => [styles.openBtn, pressed && styles.pressed]}>
                <AppText variant="buttonSmall" color={Colors.cherry}>
                  {t('labOpenFile')}
                </AppText>
              </Pressable>
            </View>
          )}
          <Pressable
            accessibilityRole="button"
            onPress={() => {
              Haptic.warning();
              onDelete(upload);
              onClose();
            }}
            style={({ pressed }) => [styles.deleteBtn, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={Colors.onCherry}>
              {t('labDelete')}
            </AppText>
          </Pressable>
        </View>
      )}
    </Modal>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  shrink: { flexShrink: 1 },
  pressed: { opacity: 0.7 },
  off: { opacity: 0.4 },
  card: { gap: 10, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  actions: { flexDirection: 'row', gap: 8 },
  action: { flex: 1, minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 10, borderRadius: 24, backgroundColor: Colors.cherry },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 52 },
  line: { borderTopWidth: 1, borderTopColor: Colors.bandLine, paddingTop: 10 },
  thumb: { width: 44, height: 44, borderRadius: 10, backgroundColor: Colors.cherryMist },
  fileThumb: { alignItems: 'center', justifyContent: 'center' },
  more: { alignSelf: 'flex-start', minHeight: 32, justifyContent: 'center' },
  viewer: { flex: 1, gap: 12, paddingHorizontal: 16, backgroundColor: 'rgba(20, 8, 14, 0.96)' },
  viewerHead: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  viewerBtn: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  full: { flex: 1, width: '100%' },
  fileView: { alignItems: 'center', justifyContent: 'center', gap: 16 },
  openBtn: { minHeight: 48, justifyContent: 'center', paddingHorizontal: 22, borderRadius: 24, backgroundColor: Colors.surface },
  deleteBtn: { alignSelf: 'center', minHeight: 44, justifyContent: 'center', paddingHorizontal: 22, borderRadius: 22, borderWidth: 1.5, borderColor: Colors.onCherry },
});
