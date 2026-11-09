import { useEffect, useRef, useState } from 'react';
import { Image, Pressable, StyleSheet, TextInput, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  FadeInDown,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { MissingMother } from '@/components/mother/missing-mother';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor } from '@/constants/typography';
import { useMother } from '@/data/store';
import { SUGGESTED, taraAnswer } from '@/data/tara-replies';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { TARA_FALLBACK, tx } from '@/i18n/content';
import { format } from '@/i18n/strings';

// Speak to Tara: the MOTHER's companion (Sakhi is the ASHA's check-in voice;
// they never share a screen or a look). Tara is drawn as the tara leaf in a
// soft rose halo that breathes while she thinks. Replies are text only
// (read-aloud lives in the ASHA's Sakhi). Ask by tapping a suggestion or
// typing; there is no microphone in the mother's app.
// Urgent topics always answer "call 108 / your ASHA" first, in cherry.

type Msg = { id: number; from: 'me' | 'tara'; text: string; urgent?: boolean };
type Mode = 'idle' | 'thinking';

const LEAF = require('@/assets/images/logo-layers/tara-leaf.webp');

function TaraAvatar({ mode, size = 88 }: { mode: Mode; size?: number }) {
  const reduceMotion = useReducedMotion();
  const halo = useSharedValue(0);
  useEffect(() => {
    cancelAnimation(halo);
    if (reduceMotion || mode === 'idle') {
      halo.set(withTiming(0, { duration: 300 }));
      return;
    }
    const ms = 450;
    halo.set(withRepeat(withSequence(withTiming(1, { duration: ms, easing: Easing.inOut(Easing.sin) }), withTiming(0, { duration: ms, easing: Easing.inOut(Easing.sin) })), -1));
  }, [halo, mode, reduceMotion]);
  const outer = useAnimatedStyle(() => ({ opacity: 0.25 + 0.45 * halo.get(), transform: [{ scale: 1 + 0.18 * halo.get() }] }));
  return (
    <View style={{ width: size * 1.3, height: size * 1.3, alignItems: 'center', justifyContent: 'center' }} aria-hidden>
      <Animated.View style={[styles.halo, { width: size * 1.2, height: size * 1.2, borderRadius: size * 0.6 }, outer]} />
      <View style={[styles.avatar, { width: size, height: size, borderRadius: size / 2 }]}>
        <Image source={LEAF} style={{ width: size * 0.26, height: size * 0.26 * (165 / 63) }} resizeMode="contain" fadeDuration={0} />
      </View>
    </View>
  );
}

export default function Talk() {
  const { demoMother, language } = useOnboarding();
  const t = useT();
  const mother = useMother(demoMother);
  const reduceMotion = useReducedMotion();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [draft, setDraft] = useState('');
  const [mode, setMode] = useState<Mode>('idle');
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const list = timers.current;
    return () => {
      list.forEach(clearTimeout);
    };
  }, []);

  if (!mother) return <MissingMother />;
  const first = mother.name.split(' ')[0];

  const ask = (text: string) => {
    const q = text.trim();
    if (!q || mode === 'thinking') return;
    Haptic.light();
    // Her own language in, her own language out (the fallback names her ASHA).
    const r = taraAnswer(q, mother.asha);
    const reply = { ...r, answer: r.keys.length ? tx(r.answer, language) : format(TARA_FALLBACK[language], { asha: mother.asha }) };
    setMsgs((m) => [...m, { id: m.length, from: 'me', text: q }]);
    setDraft('');
    setMode('thinking');
    timers.current.push(
      setTimeout(() => {
        setMsgs((m) => [...m, { id: m.length, from: 'tara', text: reply.answer, urgent: reply.urgent }]);
        if (reply.urgent) Haptic.warning();
        setMode('idle');

      }, 750),
    );
  };

  const status = mode === 'thinking' ? t('taraThinking') : t('taraAsk');
  const canSend = !!draft.trim() && mode !== 'thinking';

  return (
    <Screen
      inTabs
      followContent
      eyebrow={t('taraEyebrow')}
      title={t('taraTitle')}
      titleVariant="greeting"
      compactTitle="Tara"

    >
      <View style={styles.hero}>
        <TaraAvatar mode={mode} />
        <View style={styles.flex}>
          <AppText variant="heading">{t('taraHello', { name: first })}</AppText>
          <AppText variant="caption" color={Colors.inkMuted} accessibilityLiveRegion="polite">
            {status}
          </AppText>
        </View>
      </View>

      {msgs.length === 0 && (
        <View style={styles.chips}>
          {SUGGESTED.map((s) => (
            <Pressable
              key={s}
              accessibilityRole="button"
              onPress={() => ask(tx(s, language))}
              style={({ pressed }) => [styles.chip, pressed && styles.pressed]}
            >
              <AppText variant="buttonSmall" color={Colors.ink}>
                {tx(s, language)}
              </AppText>
            </Pressable>
          ))}
        </View>
      )}

      <View style={styles.thread}>
        {msgs.map((m) => (
          <Animated.View
            key={m.id}
            entering={reduceMotion ? undefined : FadeInDown.springify().damping(25).stiffness(250)}
            style={[styles.bubble, m.from === 'me' ? styles.mine : styles.tara, m.urgent && styles.urgent]}
            accessibilityLabel={`${m.from === 'me' ? t('taraYou') : 'Tara'}: ${m.text}`}
          >
            <AppText variant={m.urgent ? 'bodyStrong' : 'body'} color={m.urgent ? Colors.cherry : Colors.ink}>
              {m.text}
            </AppText>
          </Animated.View>
        ))}
      </View>

      <View style={styles.composer}>
        <TextInput
          value={draft}
          onChangeText={setDraft}
          placeholder={t('taraPlaceholder')}
          placeholderTextColor={Colors.inkSubtle}
          selectionColor={Colors.cherry}
          returnKeyType="send"
          onSubmitEditing={() => ask(draft)}
          accessibilityLabel={t('taraInputA11y')}
          style={[styles.input, fontFor('input')]}
          maxFontSizeMultiplier={1.4}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={t('taraSend')}
          accessibilityState={{ disabled: !canSend }}
          disabled={!canSend}
          onPress={() => ask(draft)}
          style={({ pressed }) => [styles.send, !canSend && styles.sendOff, pressed && styles.pressed]}
        >
          <Icon name="send" size={22} color={Colors.onCherry} weight="fill" />
        </Pressable>
      </View>
      <AppText variant="caption" color={Colors.inkMuted}>
        {t('taraNote')}
      </AppText>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 8, paddingRight: 16, borderRadius: 24, backgroundColor: Colors.roseTint },
  halo: { position: 'absolute', backgroundColor: Colors.blush },
  avatar: { alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 44, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 22, borderWidth: 1.5, borderColor: Colors.blush, backgroundColor: Colors.surface },
  thread: { gap: 10 },
  bubble: { maxWidth: '88%', paddingHorizontal: 14, paddingVertical: 10, borderRadius: 18 },
  mine: { alignSelf: 'flex-end', backgroundColor: Colors.cherryMist, borderBottomRightRadius: 6 },
  tara: { alignSelf: 'flex-start', backgroundColor: Colors.shell, borderBottomLeftRadius: 6 },
  urgent: { borderWidth: 2, borderColor: Colors.cherry, backgroundColor: Colors.surface },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  input: {
    flex: 1,
    minHeight: 52,
    paddingHorizontal: 16,
    borderRadius: 26,
    borderWidth: 1.5,
    borderColor: Colors.outline,
    color: Colors.ink,
    backgroundColor: Colors.surface,
  },
  send: { width: 52, height: 52, borderRadius: 26, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  sendOff: { opacity: 0.4 },
});
