import { useEffect, useRef, useState } from 'react';
import { BackHandler, Linking, Platform, Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  FadeIn,
  FadeInDown,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Haptic } from '@/constants/theme';
import { demoLocation, RockerDetector, sosStore, useSos, type Incident, type RockerKey, type SosResponder } from '@/data/sos';
import type { MotherRecord } from '@/data/store';
import { useT } from '@/features/onboarding/context';

// SOS for the mother (the logic is data/sos.ts). Always on top of her app:
//   side button   a red "SOS" tab on the right edge: one tap starts it
//   rocker        volume up, down, up, down (data/sos.ts RockerDetector).
//                 Expo Go cannot read the phone's real keys, so two slim
//                 keys on the LEFT edge stand in for them (where an iPhone's
//                 volume keys are); the web takes the keyboard's volume keys
//                 (or Alt + arrow up / down). A development build feeds the
//                 real keys through react-native-volume-manager.
//   overlay       full screen, reds only:
//                   countdown  3 s with a pulsing SOS and ripples: "Send now"
//                              or "Cancel" (a pocket press is not an alert)
//                   alerting   a radar sweep and the ASHAs near her, each
//                              one's state live: alerting, alerted, seen,
//                              on the way
//                   coming     who is coming and when; call her or 108
// Reduced motion: no ripples, sweep or pulse; the states still change.

const RED = '#B3122C';
const RED_DEEP = '#7E0A1D';
const RED_BRIGHT = '#E5243F';
const WHITE = '#FFFFFF';
const WHITE_SOFT = 'rgba(255,255,255,0.82)';
const CARD = 'rgba(255,255,255,0.12)';

export function SosLayer({ mother }: { mother: MotherRecord | undefined }) {
  const incident = useSos();
  const detector = useRef(new RockerDetector());

  const trigger = (source: Incident['source']) => {
    if (!mother) return;
    const id = sosStore.trigger({ motherId: mother.id, motherName: mother.name, zoneId: mother.zoneId, at: demoLocation(mother.id, mother.zoneId), source });
    if (id) Haptic.heavy();
  };
  const press = (key: RockerKey, source: Incident['source']) => {
    if (detector.current.push(key, Date.now())) trigger(source);
  };
  const pressRef = useRef(press);
  useEffect(() => {
    pressRef.current = press;
  });

  // Web: the keyboard's volume keys (or Alt + arrow up / down on a laptop).
  useEffect(() => {
    if (Platform.OS !== 'web' || typeof window === 'undefined') return;
    const onKey = (e: KeyboardEvent) => {
      const key: RockerKey | null =
        e.key === 'AudioVolumeUp' || (e.altKey && e.key === 'ArrowUp') ? 'up' : e.key === 'AudioVolumeDown' || (e.altKey && e.key === 'ArrowDown') ? 'down' : null;
      if (key) pressRef.current(key, 'keys');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const active = !!incident;
  return (
    <>
      {!active && <SideKeys onKey={(k) => press(k, 'rocker')} />}
      {!active && <SosTab onPress={() => trigger('button')} />}
      {incident && <SosOverlay incident={incident} />}
    </>
  );
}

/** The red tab on the right edge. A slow glow says "here if you need it". */
function SosTab({ onPress }: { onPress: () => void }) {
  const t = useT();
  const reduceMotion = useReducedMotion();
  const glow = useSharedValue(0);
  useEffect(() => {
    if (reduceMotion) return;
    glow.set(withRepeat(withTiming(1, { duration: 1600, easing: Easing.inOut(Easing.sin) }), -1, true));
    return () => cancelAnimation(glow);
  }, [glow, reduceMotion]);
  const halo = useAnimatedStyle(() => ({ opacity: 0.25 + 0.35 * glow.get(), transform: [{ scale: 1 + 0.12 * glow.get() }] }));
  return (
    <View style={styles.tabWrap} pointerEvents="box-none">
      <Animated.View style={[styles.tabHalo, halo]} pointerEvents="none" />
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${t('sosButton')}. ${t('sosButtonHint')}`}
        onPress={onPress}
        hitSlop={8}
        style={({ pressed }) => [styles.tab, pressed && { backgroundColor: RED_DEEP }]}
      >
        <Icon name="warning" size={18} color={WHITE} weight="fill" />
        <AppText variant="label" color={WHITE} style={styles.tabText}>
          {t('sosButton')}
        </AppText>
      </Pressable>
    </View>
  );
}

/** Stand-ins for the phone's volume keys (left edge, where an iPhone's are). */
function SideKeys({ onKey }: { onKey: (k: RockerKey) => void }) {
  const t = useT();
  return (
    <View style={styles.keys} pointerEvents="box-none">
      {(['up', 'down'] as const).map((k) => (
        <Pressable
          key={k}
          accessibilityRole="button"
          accessibilityLabel={`${k === 'up' ? 'Volume up' : 'Volume down'}. ${t('sosRockerHint')}`}
          onPressIn={() => {
            Haptic.select();
            onKey(k);
          }}
          hitSlop={{ top: 6, bottom: 6, left: 4, right: 18 }}
          style={({ pressed }) => [styles.key, pressed && styles.keyDown]}
        />
      ))}
    </View>
  );
}

// ---- the overlay ---------------------------------------------------------------

function useSecondsLeft(endsAt: number, on: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    const timer = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(timer);
  }, [on]);
  return Math.max(0, Math.ceil((endsAt - now) / 1000));
}

function SosOverlay({ incident }: { incident: Incident }) {
  const t = useT();
  const insets = useSafeAreaInsets();
  const reduceMotion = useReducedMotion();
  const { stage } = incident;
  const counting = stage === 'countdown';
  const left = useSecondsLeft(incident.countdownEndsAt, counting);
  const helper = incident.responders.find((r) => r.id === incident.acceptedBy);

  // A heavy tap each second of the countdown; a warning when the alert goes out.
  const lastTick = useRef(-1);
  useEffect(() => {
    if (counting && left !== lastTick.current) {
      lastTick.current = left;
      if (left > 0) Haptic.medium();
    }
  }, [counting, left]);
  useEffect(() => {
    if (stage === 'alerting') Haptic.warning();
    if (stage === 'coming') Haptic.success();
    if (stage === 'cancelled' || stage === 'resolved') {
      const timer = setTimeout(() => sosStore.dismiss(), 2600);
      return () => clearTimeout(timer);
    }
  }, [stage]);

  // Android back: cancels a countdown; otherwise the overlay stays (help is coming).
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (stage === 'countdown') sosStore.cancel();
      return true;
    });
    return () => sub.remove();
  }, [stage]);

  const call = (number: string) => Linking.openURL(`tel:${number}`).catch(() => {});
  const shown = incident.responders.filter((r) => r.state !== 'queued' || r.wave === Math.max(1, incident.wave));
  const done = stage === 'cancelled' || stage === 'resolved';

  return (
    <Animated.View entering={reduceMotion ? undefined : FadeIn.duration(180)} style={[StyleSheet.absoluteFill, styles.overlay]} accessibilityViewIsModal accessibilityLiveRegion="assertive">
      <View style={[styles.inner, { paddingTop: insets.top + 16, paddingBottom: Math.max(insets.bottom, 16) + 8 }]}>
        <AppText variant="overline" color={WHITE_SOFT} align="center">
          {t('sosTitle').toUpperCase()}
        </AppText>

        <View style={styles.stage}>
          {stage === 'alerting' || stage === 'coming' ? <Radar on={stage === 'alerting' && !reduceMotion} /> : <Ripples on={counting && !reduceMotion} />}
          <Pulse on={counting && !reduceMotion}>
            {stage === 'coming' ? (
              <Icon name="checkCircle" size={64} color={RED} weight="fill" />
            ) : (
              <AppText variant="display" color={RED} style={styles.sosText}>
                {counting && left > 0 ? String(left) : 'SOS'}
              </AppText>
            )}
          </Pulse>
        </View>

        <View style={styles.words} accessibilityLiveRegion="polite">
          <AppText variant="title" color={WHITE} align="center">
            {counting
              ? t('sosCountdown', { n: left })
              : stage === 'alerting'
                ? t('sosReporting')
                : stage === 'coming' && helper
                  ? t('sosComing', { name: helper.name })
                  : stage === 'cancelled'
                    ? t('sosCancelled')
                    : t('sosResolved')}
          </AppText>
          {stage === 'alerting' && (
            <AppText variant="body" color={WHITE_SOFT} align="center">
              {t('sosWave', { n: incident.wave, count: incident.responders.filter((r) => r.wave === incident.wave).length })}
            </AppText>
          )}
          {stage === 'coming' && helper && (
            <AppText variant="body" color={WHITE_SOFT} align="center">
              {t('sosComingEta', { min: helper.etaMin })}
            </AppText>
          )}
        </View>

        {(stage === 'alerting' || stage === 'coming') && (
          <View style={styles.list}>
            {shown.slice(0, 4).map((r, i) => (
              <ResponderRow key={r.id} r={r} index={i} />
            ))}
          </View>
        )}

        <View style={styles.flex} />

        {counting && (
          <View style={styles.row}>
            <Big label={t('sosCancel')} onPress={() => sosStore.cancel()} outline />
            <Big label={t('sosSendNow')} onPress={() => sosStore.sendNow()} />
          </View>
        )}
        {(stage === 'alerting' || stage === 'coming') && (
          <View style={styles.actions}>
            {stage === 'coming' && helper ? <Big label={t('sosCallAsha', { name: helper.name })} icon="phone" onPress={() => call(helper.phone)} /> : null}
            <Big label={t('sosCall108')} icon="phone" onPress={() => call('108')} outline={stage === 'coming'} />
            <View style={styles.row}>
              <Big label={t('sosSafe')} onPress={() => sosStore.cancel()} outline small />
              {stage === 'coming' && <Big label={t('sosHelpArrived')} onPress={() => sosStore.resolve()} outline small />}
            </View>
          </View>
        )}
        {done && <Big label={t('sosClose')} onPress={() => sosStore.dismiss()} outline />}
      </View>
    </Animated.View>
  );
}

function ResponderRow({ r, index }: { r: SosResponder; index: number }) {
  const t = useT();
  const reduceMotion = useReducedMotion();
  const label = { queued: t('sosQueued'), alerting: t('sosAlerting'), alerted: t('sosAlerted'), seen: t('sosSeen'), coming: t('sosOnWay') }[r.state];
  const strong = r.state === 'coming' || r.state === 'seen';
  return (
    <Animated.View entering={reduceMotion ? undefined : FadeInDown.duration(220).delay(index * 90)} style={[styles.card, r.state === 'coming' && styles.cardComing]}>
      <View style={styles.avatar}>
        <AppText variant="heading" color={RED}>
          {r.name.slice(0, 1)}
        </AppText>
      </View>
      <View style={styles.flex}>
        <AppText variant="bodyStrong" color={WHITE} numberOfLines={1}>
          {r.own ? `${r.name} · ${t('sosOwn')}` : r.name}
        </AppText>
        <AppText variant="caption" color={WHITE_SOFT}>
          {t('sosKm', { km: r.km.toFixed(1), min: r.etaMin })}
        </AppText>
      </View>
      <View style={[styles.chip, strong && styles.chipStrong]}>
        <AppText variant="label" color={strong ? RED : WHITE}>
          {label}
        </AppText>
      </View>
    </Animated.View>
  );
}

function Big({ label, onPress, outline, small, icon }: { label: string; onPress: () => void; outline?: boolean; small?: boolean; icon?: 'phone' }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={() => {
        Haptic.medium();
        onPress();
      }}
      style={({ pressed }) => [styles.big, small && styles.bigSmall, outline ? styles.bigOutline : styles.bigSolid, pressed && styles.pressed]}
    >
      {icon && <Icon name={icon} size={20} color={outline ? WHITE : RED} weight="fill" />}
      <AppText variant={small ? 'buttonSmall' : 'button'} color={outline ? WHITE : RED} numberOfLines={1} style={styles.shrink}>
        {label}
      </AppText>
    </Pressable>
  );
}

/** The white disc in the middle; beats during the countdown. */
function Pulse({ on, children }: { on: boolean; children: React.ReactNode }) {
  const beat = useSharedValue(0);
  useEffect(() => {
    if (!on) {
      cancelAnimation(beat);
      beat.set(withTiming(0, { duration: 150 }));
      return;
    }
    beat.set(withRepeat(withSequence(withTiming(1, { duration: 160, easing: Easing.out(Easing.quad) }), withTiming(0, { duration: 840, easing: Easing.inOut(Easing.quad) })), -1));
    return () => cancelAnimation(beat);
  }, [beat, on]);
  const style = useAnimatedStyle(() => ({ transform: [{ scale: 1 + 0.07 * beat.get() }] }));
  return <Animated.View style={[styles.disc, style]}>{children}</Animated.View>;
}

/** Rings that spread out from the disc, like a call going out. */
function Ripples({ on }: { on: boolean }) {
  return (
    <>
      {[0, 1, 2].map((i) => (
        <Ring key={i} delay={i * 600} on={on} />
      ))}
    </>
  );
}

function Ring({ delay, on }: { delay: number; on: boolean }) {
  const p = useSharedValue(0);
  useEffect(() => {
    if (!on) {
      cancelAnimation(p);
      p.set(0);
      return;
    }
    p.set(withDelay(delay, withRepeat(withTiming(1, { duration: 1800, easing: Easing.out(Easing.quad) }), -1)));
    return () => cancelAnimation(p);
  }, [delay, on, p]);
  const style = useAnimatedStyle(() => ({ opacity: p.get() === 0 ? 0 : 0.55 * (1 - p.get()), transform: [{ scale: 1 + 1.4 * p.get() }] }));
  return <Animated.View style={[styles.ring, style]} pointerEvents="none" />;
}

/** A radar: three fixed range rings and a sweeping beam, while ASHAs are being found. */
function Radar({ on }: { on: boolean }) {
  const a = useSharedValue(0);
  useEffect(() => {
    if (!on) {
      cancelAnimation(a);
      return;
    }
    a.set(0);
    a.set(withRepeat(withTiming(360, { duration: 2200, easing: Easing.linear }), -1));
    return () => cancelAnimation(a);
  }, [a, on]);
  const beam = useAnimatedStyle(() => ({ transform: [{ rotate: `${a.get()}deg` }] }));
  return (
    <View style={styles.radar} pointerEvents="none">
      {[1, 0.72, 0.44].map((s) => (
        <View key={s} style={[styles.radarRing, { transform: [{ scale: s }] }]} />
      ))}
      <Animated.View style={[styles.beamWrap, beam]}>
        <View style={styles.beam} />
        <View style={[styles.beam, styles.beamTrail1]} />
        <View style={[styles.beam, styles.beamTrail2]} />
      </Animated.View>
    </View>
  );
}

const DISC = 150;
const RADAR = 260;
const styles = StyleSheet.create({
  flex: { flex: 1 },
  shrink: { flexShrink: 1 },
  pressed: { opacity: 0.75 },
  // Side tab (right edge)
  tabWrap: { position: 'absolute', right: 0, top: '42%', alignItems: 'flex-end', justifyContent: 'center' },
  tabHalo: { position: 'absolute', right: -6, width: 58, height: 92, borderRadius: 30, backgroundColor: RED_BRIGHT },
  tab: {
    width: 46,
    height: 80,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 4,
    borderTopLeftRadius: 23,
    borderBottomLeftRadius: 23,
    backgroundColor: RED,
    shadowColor: RED_DEEP,
    shadowOpacity: 0.35,
    shadowRadius: 8,
    shadowOffset: { width: -2, height: 3 },
    elevation: 6,
  },
  tabText: { letterSpacing: 1 },
  // Mock volume keys (left edge)
  keys: { position: 'absolute', left: 0, top: '24%', gap: 10 },
  key: { width: 6, height: 46, borderTopRightRadius: 4, borderBottomRightRadius: 4, backgroundColor: 'rgba(43,27,33,0.22)' },
  keyDown: { width: 9, backgroundColor: RED_BRIGHT },
  // Overlay
  overlay: { backgroundColor: RED, zIndex: 100, elevation: 100 },
  inner: { flex: 1, paddingHorizontal: 20, gap: 14, alignItems: 'stretch' },
  stage: { height: RADAR, alignItems: 'center', justifyContent: 'center' },
  disc: {
    width: DISC,
    height: DISC,
    borderRadius: DISC / 2,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: WHITE,
    shadowColor: RED_DEEP,
    shadowOpacity: 0.5,
    shadowRadius: 24,
    elevation: 10,
  },
  sosText: { fontSize: 48, lineHeight: 56, letterSpacing: 2 },
  ring: { position: 'absolute', width: DISC, height: DISC, borderRadius: DISC / 2, borderWidth: 3, borderColor: WHITE },
  radar: { position: 'absolute', width: RADAR, height: RADAR, alignItems: 'center', justifyContent: 'center' },
  radarRing: { position: 'absolute', width: RADAR, height: RADAR, borderRadius: RADAR / 2, borderWidth: 1.5, borderColor: 'rgba(255,255,255,0.35)' },
  beamWrap: { position: 'absolute', width: RADAR, height: RADAR },
  beam: { position: 'absolute', left: RADAR / 2 - 1.5, top: 0, width: 3, height: RADAR / 2, borderRadius: 2, backgroundColor: 'rgba(255,255,255,0.85)', transformOrigin: '50% 100%' },
  beamTrail1: { opacity: 0.35, transform: [{ rotate: '-10deg' }] },
  beamTrail2: { opacity: 0.15, transform: [{ rotate: '-20deg' }] },
  words: { gap: 6, alignItems: 'center' },
  list: { gap: 8 },
  card: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 14, backgroundColor: CARD },
  cardComing: { backgroundColor: 'rgba(255,255,255,0.22)', borderWidth: 1.5, borderColor: WHITE },
  avatar: { width: 38, height: 38, borderRadius: 19, alignItems: 'center', justifyContent: 'center', backgroundColor: WHITE },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 10, borderWidth: 1, borderColor: 'rgba(255,255,255,0.6)' },
  chipStrong: { backgroundColor: WHITE, borderColor: WHITE },
  row: { flexDirection: 'row', gap: 10 },
  actions: { gap: 10 },
  big: { flex: 1, minHeight: 56, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 14, borderRadius: 28 },
  bigSmall: { minHeight: 48, borderRadius: 24 },
  bigSolid: { backgroundColor: WHITE },
  bigOutline: { borderWidth: 2, borderColor: WHITE },
});
