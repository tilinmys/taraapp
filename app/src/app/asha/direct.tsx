import { router } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';

import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Colors } from '@/constants/theme';
import { ME_ASHA, zone, zoneName } from '@/data/care-network';
import { careStatus, sortByRisk, useAshaMothers } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { goBack } from '@/lib/nav';
import { probeDirect } from '@/voice/direct';
import { vlog } from '@/voice/log';

// Sakhi Direct: the section that runs the voice check-in over ONE WebSocket
// straight to the server (sakhi_ws.py, voice/direct.ts). Works in Expo Go.
//   Connection   checked as the page opens: connected + round trip, or why
//                not, with "Test again"
//   Her women    needs care first; a tap opens the check-in in Direct mode
//                (the socket opens on its intro, so Start is instant)

type Probe = { state: 'checking' } | { state: 'ok'; rttMs: number } | { state: 'down'; why: string };

export default function SakhiDirect() {
  const t = useT();
  const { language } = useOnboarding();
  const women = sortByRisk(useAshaMothers(ME_ASHA));
  const [probe, setProbe] = useState<Probe>({ state: 'checking' });

  const check = useCallback(
    () =>
      probeDirect().then((r) => {
        vlog('live', `direct probe: ${r.ok ? `ok, ${r.rttMs} ms round trip (${r.ms} ms to connect)` : `FAILED ${r.error}`}`);
        setProbe(r.ok ? { state: 'ok', rttMs: r.rttMs ?? r.ms } : { state: 'down', why: r.error ?? '?' });
      }),
    [],
  );
  useEffect(() => {
    check();
  }, [check]);
  const test = () => {
    setProbe({ state: 'checking' });
    check();
  };

  const color = probe.state === 'ok' ? Colors.sage : probe.state === 'down' ? Colors.cherry : Colors.inkMuted;
  return (
    <Screen
      band
      eyebrow={t('directHint')}
      title={t('directTitle')}
      compactTitle={t('directTitle')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
    >
      <AppText variant="body" color={Colors.inkMuted}>
        {t('directIntro')}
      </AppText>

      <View style={styles.status} accessibilityLiveRegion="polite">
        <View style={[styles.dot, { backgroundColor: color }]} />
        <View style={styles.flex}>
          <AppText variant="label" color={Colors.inkMuted}>
            {t('directStatus')}
          </AppText>
          <AppText variant="bodyStrong" color={color}>
            {probe.state === 'ok' ? t('directOk', { ms: probe.rttMs }) : probe.state === 'down' ? t('directDown', { why: probe.why }) : t('directChecking')}
          </AppText>
        </View>
        {probe.state !== 'checking' && <PressButton label={t('directTest')} variant="link" onPress={test} />}
      </View>

      <AppText variant="heading" accessibilityRole="header">
        {t('directWho')}
      </AppText>
      <View style={styles.list}>
        {women.map((m) => {
          const z = zone(m.zoneId);
          const c = careStatus(m);
          return (
            <NavCard
              key={m.id}
              initial={m.name.slice(0, 1)}
              title={m.name}
              line={[z ? zoneName(z, language) : '', t('monthsShort', { n: m.months })].filter(Boolean).join(' · ')}
              status={c === 'alert' ? { label: t('statusNeedsCare'), tone: 'alert' } : undefined}
              onPress={() => router.push({ pathname: '/asha/session/[id]', params: { id: m.id, via: 'direct' } })}
            />
          );
        })}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  status: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 16, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  dot: { width: 12, height: 12, borderRadius: 6 },
  list: { gap: 10 },
});
