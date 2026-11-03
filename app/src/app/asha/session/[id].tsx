import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState, useSyncExternalStore } from 'react';
import { BackHandler, Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, useReducedMotion, useSharedValue } from 'react-native-reanimated';

import { ConfirmSheet } from '@/components/system/confirm-sheet';
import { Icon, type IconName } from '@/components/system/icon';
import { SakhiOrb, type OrbState } from '@/components/system/sakhi-orb';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Segmented } from '@/components/ui/segmented';
import { LiveBridge } from '@/components/voice/live-bridge';
import { Colors, Layout } from '@/constants/theme';
import { ME_ASHA } from '@/data/care-network';
import { dayPlanStore } from '@/data/day-plan';
import { ANTENATAL_QUESTIONS, questionText, RULES } from '@/data/mockScreening';
import { useAshaMothers, useMother, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import type { Lang } from '@/i18n/strings';
import { goBack } from '@/lib/nav';
import { DirectSession, type DirectSnapshot } from '@/voice/direct';
import { SakhiEngine, type Line, type Snapshot } from '@/voice/engine';
import { LiveSession, type LiveSnapshot } from '@/voice/live';

// Check-in with Sakhi: a VIEW of the question engine (voice/engine.ts), the
// single state manager. The engine owns the flow, the audio and the records;
// this screen renders its snapshot and sends commands: start, repeat, next,
// undo a flag, exit. Live (WebRTC voice agent) and on-the-phone check-ins
// look and behave the same: only the badge differs.
//   Intro     the orb, what will happen, Sakhi's language, live or not, Start
//   Talking   a COMPACT panel pinned on top (orb + one clear state, progress,
//             the question, Repeat / Next) that keeps ONE height in every
//             stage: the question card has a fixed height and the card and
//             controls stay laid out (disabled) while Sakhi prepares or
//             finishes, so nothing under it ever jumps. No large title during
//             the conversation (Screen hideTitle): the nav bar names her, and
//             nothing half-scrolled hides behind the panel. Underneath, the conversation as
//             a chat thread, oldest first. Each line is keyed: an answer
//             bubble only ever updates itself ("Writing it down…" -> text),
//             never another line. The thread follows new lines only while
//             the ASHA is at the bottom (Screen followContent): scrolling up
//             to read an earlier answer is never yanked away.
//   Done      Sakhi says she is done and the report opens by itself
// ?via=direct (the Sakhi Direct section): the same conversation, with the
// voice over ONE WebSocket to the server (voice/direct.ts) instead of the
// WebRTC page. The socket opens on the intro, so by Start it is connected
// and Sakhi's lines are already on the phone.

const TOTAL = ANTENATAL_QUESTIONS.length;
const INTRO_STEPS = [
  { icon: 'speaker', text: 'sStep1' },
  { icon: 'mic', text: 'sStep2' },
  { icon: 'shield', text: 'sStep3' },
] as const;
const AUTO_OPEN_MS = 1200;
/** Characters past which a question uses the smaller bold body size (3 heading lines hold ~60). */
const LONG_QUESTION = 60;

export default function Session() {
  const { id, via } = useLocalSearchParams<{ id: string; via?: string }>();
  const mother = useMother(id);
  if (!mother) return null;
  return <Conversation key={mother.id} mother={mother} direct={via === 'direct'} />;
}

function Conversation({ mother, direct }: { mother: MotherRecord; direct: boolean }) {
  const { language: appLanguage } = useOnboarding();
  const t = useT();
  const women = useAshaMothers(ME_ASHA);
  // Sakhi talks to the mother in HER language (Kannada or Hindi); the screen
  // is in the ASHA's. The ASHA can switch it on the intro.
  const [lang, setLang] = useState<Lang>(() => {
    const preferred = mother.language ?? appLanguage;
    return preferred === 'hi' || preferred === 'kn' ? preferred : 'kn';
  });
  const reduceMotion = useReducedMotion();
  // One engine (and live connection) per language, created ONLY when the
  // language changes: the mother record changes during the check-in.
  const [engine, setEngine] = useState(() => new SakhiEngine(mother, lang, { warm: !direct }));
  const [live, setLive] = useState(() => new LiveSession(mother, lang));
  const [ws, setWs] = useState(() => new DirectSession(lang));
  const chooseLang = (next: Lang) => {
    setLang(next);
    setEngine(new SakhiEngine(mother, next, { warm: !direct }));
    setLive(new LiveSession(mother, next));
    setWs(new DirectSession(next));
  };
  const snap: Snapshot = useSyncExternalStore(engine.subscribe, engine.getSnapshot, engine.getSnapshot);
  const liveSnap: LiveSnapshot = useSyncExternalStore(live.subscribe, live.getSnapshot, live.getSnapshot);
  const wsSnap: DirectSnapshot = useSyncExternalStore(ws.subscribe, ws.getSnapshot, ws.getSnapshot);
  const [exitOpen, setExitOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [demoNext, setDemoNext] = useState<'normal' | 'danger'>('normal');
  const sheet = useSharedValue(0);

  // Mic permission + gateway check while the ASHA reads the intro; release on leave.
  useEffect(() => {
    engine.prepare();
    return () => engine.release();
  }, [engine]);
  // Live: is the voice agent up; pre-warm the WebRTC connection meanwhile.
  useEffect(() => {
    if (!direct) live.prepare();
    return () => live.release();
  }, [direct, live]);
  // Sakhi Direct: open the socket now; her lines arrive while the ASHA reads.
  useEffect(() => {
    if (direct) ws.connect(engine.allLines()).catch(() => {});
    return () => ws.release();
  }, [direct, engine, ws]);

  // Android back: ask before leaving, exactly like the Exit button.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      setExitOpen(true);
      return true;
    });
    return () => sub.remove();
  }, []);

  const finish = () => {
    const report = engine.finish();
    live.end();
    ws.release();
    // A finished check-in IS a visit: tick it on today's list for her.
    dayPlanStore.markVisited(ME_ASHA, women, mother.id);
    router.replace({ pathname: '/asha/summary/[id]', params: { id: mother.id, report } });
  };

  // Done: the report opens by itself (no tap needed to keep the flow going).
  useEffect(() => {
    if (snap.stage !== 'done') return;
    const timer = setTimeout(finish, AUTO_OPEN_MS);
    return () => clearTimeout(timer);
  }, [snap.stage]); // eslint-disable-line react-hooks/exhaustive-deps

  const exitNow = () => {
    setExitOpen(false);
    live.end();
    ws.release();
    engine.exit();
    router.dismissTo({ pathname: '/asha/mother/[id]', params: { id: mother.id } });
  };

  const intro = snap.stage === 'idle';
  const held = snap.held;
  const talking = !held && (snap.stage === 'asking' || snap.stage === 'listening' || snap.stage === 'hearing');
  const listening = !held && (snap.stage === 'listening' || snap.stage === 'hearing');
  const isLive = snap.transport !== 'phone' && !snap.liveLost;
  // Clamped: the engine's index can point one past the last question while finishing.
  const q = ANTENATAL_QUESTIONS[Math.min(snap.idx, TOTAL - 1)];
  const level = Math.min(1, Math.max(0, snap.level));
  const status = held
    ? t('vHeld')
    : snap.stage === 'preparing'
      ? (direct ? wsSnap.stage === 'connecting' : liveSnap.stage === 'connecting')
        ? t('vConnecting')
        : liveSnap.stage === 'joining'
          ? t('vJoining')
          : t('vPreparing')
      : snap.stage === 'asking'
        ? t('sAsking')
        : snap.stage === 'listening'
          ? t('vSpeakNow')
          : snap.stage === 'hearing'
            ? t('vHearing')
            : snap.stage === 'finishing'
              ? t('vFinishing')
              : snap.stage === 'done'
                ? t('vOpening')
                : t('sReady');
  const orb: OrbState = held ? 'idle' : snap.stage === 'asking' ? 'speaking' : listening ? 'listening' : snap.stage === 'finishing' || snap.stage === 'preparing' ? 'thinking' : 'idle';

  const cta = intro
    ? {
        label: t('sStart'),
        enabled: direct ? wsSnap.stage !== 'idle' : liveSnap.available !== null,
        icon: 'mic' as const,
        haptic: 'medium' as const,
        // Direct: the socket, even if it is still connecting (Start waits for
        // it, and falls back to the phone if the server cannot be reached).
        onPress: () => engine.start(direct ? ws : liveSnap.available ? live : undefined),
      }
    : held
      ? { label: t('vContinue'), enabled: true, icon: 'mic' as const, haptic: 'medium' as const, onPress: () => engine.continueAfterHold() }
      : snap.stage === 'done'
      ? { label: t('sFinish'), enabled: true, icon: 'checkCircle' as const, haptic: 'success' as const, onPress: finish }
      : null;

  return (
    <Screen
      eyebrow={mother.name}
      title={t('talkSakhi')}
      compactTitle={intro ? t('talkSakhi') : mother.name}
      hideTitle={!intro}
      back={{ label: t('sExit'), icon: 'close', onPress: () => (intro ? goBack({ pathname: '/asha/mother/[id]', params: { id: mother.id } }) : setExitOpen(true)) }}
      sheetProgress={sheet}
      band={!intro}
      stickyAccessory
      followContent={!intro}
      accessory={
        intro ? undefined : (
          <View style={styles.panel}>
            <View style={styles.head}>
              <View style={styles.orbWrap}>
                {listening && (
                  // Grows with her voice, never past its own box (it used to
                  // spill over the status text at full volume).
                  <View aria-hidden style={[styles.ring, { transform: [{ scale: 1 + level * 0.12 }], opacity: 0.25 + level * 0.55 }]} />
                )}
                <SakhiOrb state={orb} size={52} />
              </View>
              <View style={styles.flex} accessibilityLiveRegion="polite">
                <AppText variant="heading" color={listening ? Colors.sage : Colors.ink} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.8}>
                  {held ? t('vHeldShort') : status}
                </AppText>
                <View style={styles.meta}>
                  <AppText variant="caption" color={snap.stage === 'hearing' ? Colors.sage : Colors.inkMuted} numberOfLines={1} style={styles.flex}>
                    {snap.stage === 'hearing' ? t('vNextHint') : t('sQuestionOf', { n: Math.min(snap.idx + 1, TOTAL), total: TOTAL })}
                  </AppText>
                  <VoiceBadge snap={snap} t={t} />
                </View>
              </View>
            </View>
            <View style={styles.track} accessibilityRole="progressbar" aria-valuenow={Math.round((snap.answered / TOTAL) * 100)}>
              <View style={[styles.fill, { transform: [{ scaleX: snap.answered / TOTAL }] }]} />
            </View>

            {/* Always there: the question while talking, else what Sakhi is doing.
                It never clips: a long question gets the smaller bold body size
                (and shrinks further on the phone if it still needs more), and
                the card grows past its usual height rather than cut the text. */}
            <View style={[styles.questionCard, listening && styles.questionCardListening]}>
              {talking ? (
                <>
                  <AppText variant={questionText(q, lang).length > LONG_QUESTION ? 'bodyStrong' : 'heading'} numberOfLines={4} adjustsFontSizeToFit minimumFontScale={0.8}>
                    {questionText(q, lang)}
                  </AppText>
                  {lang !== 'en' && (
                    <AppText variant="caption" color={Colors.inkMuted} numberOfLines={2} style={styles.noShrink}>
                      {q.en}
                    </AppText>
                  )}
                </>
              ) : (
                <AppText variant="body" color={Colors.inkMuted} align="center" numberOfLines={4} adjustsFontSizeToFit minimumFontScale={0.8}>
                  {status}
                </AppText>
              )}
            </View>

            <View style={styles.controls}>
              <ControlButton icon="reset" label={t('vRepeat')} onPress={() => engine.repeat()} disabled={!talking} />
              <ControlButton icon="forward" label={t('vNext')} onPress={() => engine.next()} primary disabled={!listening} />
            </View>
          </View>
        )
      }
      cta={cta}
      overlay={
        <>
          {/* Live Sakhi's WebRTC connection (invisible); loaded on the intro to pre-warm. */}
          {!direct && <LiveBridge session={live} />}
          <ConfirmSheet
            open={exitOpen}
            progress={sheet}
            title={t('sExitQ')}
            body={t('sExitBody', { name: mother.name })}
            confirmLabel={t('sExitYes')}
            cancelLabel={t('sKeep')}
            onConfirm={exitNow}
            onCancel={() => setExitOpen(false)}
          />
        </>
      }
    >
      {intro && (
        <Animated.View entering={reduceMotion ? undefined : FadeIn.duration(320)} style={styles.intro}>
          <SakhiOrb state="idle" size={150} />
          <AppText variant="heading" align="center">
            {t('sIntro', { name: mother.name })}
          </AppText>
          <View style={styles.steps}>
            {INTRO_STEPS.map((x, i) => (
              <View key={x.text} style={styles.stepRow}>
                <View style={styles.stepNum}>
                  <AppText variant="label" color={Colors.pumpkinInk}>
                    {`${i + 1}`}
                  </AppText>
                </View>
                <AppText variant="body" style={styles.flex}>
                  {t(x.text)}
                </AppText>
                <Icon name={x.icon} size={20} color={Colors.pumpkin} tone={Colors.cherry} />
              </View>
            ))}
          </View>
          <View style={styles.langPick}>
            <AppText variant="label" color={Colors.inkMuted} align="center">
              {t('vSpeakIn')}
            </AppText>
            <Segmented<Lang>
              options={[
                { id: 'kn', label: 'ಕನ್ನಡ' },
                { id: 'hi', label: 'हिंदी' },
              ]}
              value={lang}
              onChange={chooseLang}
            />
          </View>
          {/* Live Sakhi or on this phone: decided for her, shown plainly. */}
          {direct ? (
            <View style={styles.liveNote}>
              <View style={[styles.dot, { backgroundColor: wsSnap.stage === 'live' ? Colors.sage : wsSnap.stage === 'failed' ? Colors.pumpkin : Colors.inkMuted }]} />
              <View style={styles.flex}>
                <AppText variant="caption" color={wsSnap.stage === 'live' ? Colors.sage : Colors.inkMuted}>
                  {wsSnap.stage === 'live' ? t('vDirectReady') : wsSnap.stage === 'failed' ? t('vDirectFailed') : t('vDirectChecking')}
                </AppText>
                {wsSnap.stage === 'live' && (
                  <AppText variant="caption" color={Colors.inkMuted}>
                    {`${t('vDirectLines', { ready: wsSnap.ready, total: wsSnap.total })}${wsSnap.rttMs !== null ? ` · ${wsSnap.rttMs} ms` : ''}`}
                  </AppText>
                )}
              </View>
            </View>
          ) : (
            <View style={styles.liveNote}>
              <View style={[styles.dot, { backgroundColor: liveSnap.available ? Colors.sage : liveSnap.available === null ? Colors.inkMuted : Colors.pumpkin }]} />
              <AppText variant="caption" color={liveSnap.available ? Colors.sage : Colors.inkMuted} style={styles.flex}>
                {liveSnap.available === null ? t('vLiveChecking') : liveSnap.available ? t('vLiveReady') : t('vLiveOfflineOnly')}
              </AppText>
            </View>
          )}
          <AppText variant="caption" color={Colors.inkMuted} align="center">
            {t('sHaveHer', { name: mother.name.split(' ')[0] })}
          </AppText>
          {snap.micDenied && (
            <AppText variant="caption" color={Colors.cherry} align="center">
              {t('vMicDenied')}
            </AppText>
          )}
        </Animated.View>
      )}

      {/* Notes: only when something is not as it should be. */}
      {!intro && snap.liveLost && <Note icon="offline" text={t('vLiveLost')} />}
      {!intro && snap.voice === 'backup' && <Note icon="warning" text={t('vBackupNote')} />}
      {!intro && snap.offline && <Note icon="offline" text={t('vOffline')} />}
      {!intro && isLive && (direct ? wsSnap.stage === 'reconnecting' : liveSnap.reconnecting) && <Note icon="offline" text={t('vReconnecting')} />}
      {!intro && isLive && liveSnap.needsTap && (
        <Pressable accessibilityRole="button" onPress={() => live.resumeAudio()} style={({ pressed }) => [styles.tap, pressed && styles.pressed]}>
          <Icon name="speaker" size={20} color={Colors.surface} />
          <AppText variant="buttonSmall" color={Colors.surface}>
            {t('vTapToHear')}
          </AppText>
        </Pressable>
      )}

      {/* Danger flags: inline, with Undo; the conversation goes on. */}
      {snap.alerts.map((a) => (
        <Animated.View key={a.idx} entering={reduceMotion ? undefined : FadeInDown.duration(220)} style={styles.alert} accessibilityLiveRegion="assertive">
          <Icon name="warning" size={22} color={Colors.cherry} />
          <View style={styles.flex}>
            <AppText variant="bodyStrong" color={Colors.cherry}>
              {`${t('vFlagged')} · ${RULES[a.code].title}`}
            </AppText>
            <AppText variant="caption" color={Colors.ink} numberOfLines={2}>
              {a.text}
            </AppText>
          </View>
          <Pressable accessibilityRole="button" accessibilityLabel={`${t('vUndo')}: ${RULES[a.code].title}`} onPress={() => engine.undoAlert(a.idx)} style={({ pressed }) => [styles.undo, pressed && styles.pressed]}>
            <AppText variant="buttonSmall" color={Colors.cherry}>
              {t('vUndo')}
            </AppText>
          </Pressable>
        </Animated.View>
      ))}

      {/* The conversation as text: only in demo mode (no microphone), where it
          is the only way to follow along. In a real check-in she listens and
          speaks; the panel above shows the one question that matters now. */}
      {snap.mode === 'demo' && !isLive && snap.lines.length > 0 && (
        <View style={styles.thread}>
          <AppText variant="overline" color={Colors.inkMuted}>
            {`${t('sConversation')} · ${snap.answered}/${TOTAL}`.toLocaleUpperCase()}
          </AppText>
          {snap.lines.map((l) => (
            <Bubble key={l.key} line={l} t={t} reduceMotion={reduceMotion} />
          ))}
        </View>
      )}

      {/* Testing tools: only without a microphone or speech service. */}
      {!intro && snap.mode === 'demo' && !isLive && snap.stage !== 'done' && snap.stage !== 'finishing' && (
        <View style={styles.tools}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Testing tools"
            accessibilityState={{ expanded: toolsOpen }}
            onPress={() => setToolsOpen((o) => !o)}
            style={styles.toolsHead}
          >
            <Icon name="settings" size={18} color={Colors.inkMuted} />
            <AppText variant="label" color={Colors.inkMuted} style={styles.flex}>
              {t('vDemo')}
            </AppText>
            <Icon name={toolsOpen ? 'close' : 'down'} size={18} color={Colors.inkMuted} />
          </Pressable>
          {toolsOpen && (
            <View style={styles.toolsBody}>
              <AppText variant="caption" color={Colors.inkMuted}>
                Next danger-sign answer:
              </AppText>
              <Segmented
                options={[
                  { id: 'normal', label: 'Normal' },
                  { id: 'danger', label: '⚠️ Danger sign' },
                ]}
                value={demoNext}
                onChange={(v) => {
                  setDemoNext(v);
                  engine.setDemoNext(v);
                }}
              />
              <PressButton label="Fill the rest as normal" variant="link" onPress={() => engine.fillRemaining()} />
            </View>
          )}
        </View>
      )}
    </Screen>
  );
}

/** One line of the conversation. Sakhi on the left, the ASHA on the right. */
function Bubble({ line, t, reduceMotion }: { line: Line; t: ReturnType<typeof useT>; reduceMotion: boolean }) {
  const mine = line.from === 'asha';
  const soft = line.pending || line.offline;
  return (
    <Animated.View entering={reduceMotion ? undefined : FadeInDown.duration(180)} style={[styles.row, mine && styles.rowMine]}>
      {!mine && (
        <View style={[styles.avatar, line.danger && styles.avatarDanger]} aria-hidden>
          <Icon name={line.danger ? 'warning' : 'chat'} size={14} color={line.danger ? Colors.surface : Colors.pumpkin} tone={Colors.cherry} />
        </View>
      )}
      <View
        style={[styles.bubble, mine ? styles.bubbleMine : styles.bubbleSakhi, line.danger && styles.bubbleDanger, soft && styles.bubbleSoft]}
        accessibilityLabel={`${mine ? t('sYou') : 'Sakhi'}: ${line.text}`}
      >
        {!mine && line.idx !== undefined && (
          <AppText variant="label" color={Colors.inkMuted}>
            {`Q${line.idx + 1}`}
          </AppText>
        )}
        <AppText variant={mine ? 'bodyStrong' : 'body'} color={soft ? Colors.inkMuted : line.danger ? Colors.cherry : Colors.ink}>
          {`${line.danger && mine ? '⚠️ ' : ''}${line.text}`}
        </AppText>
        {!!line.sub && (
          <AppText variant="caption" color={Colors.inkMuted}>
            {line.sub}
          </AppText>
        )}
      </View>
    </Animated.View>
  );
}

/** Whose voice the ASHA hears, and how it travels. Never a silent swap. */
function VoiceBadge({ snap, t }: { snap: Snapshot; t: ReturnType<typeof useT> }) {
  const backup = snap.voice === 'backup';
  const live = snap.transport !== 'phone' && !snap.liveLost;
  const label = backup ? t('vBackupVoice') : live ? t('vLiveTitle') : snap.voice === 'sakhi' ? t('vSakhiVoice') : snap.mode === 'live' ? t('vLive') : 'Demo';
  const good = !backup && (live || snap.voice === 'sakhi' || snap.mode === 'live');
  const color = backup ? Colors.pumpkinInk : good ? Colors.sage : Colors.inkMuted;
  return (
    <View style={[styles.badge, { backgroundColor: backup ? Colors.orangeTint : good ? Colors.sageTint : Colors.shell }]}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <AppText variant="label" color={color} numberOfLines={1}>
        {label}
      </AppText>
    </View>
  );
}

function Note({ icon, text }: { icon: IconName; text: string }) {
  return (
    <View style={styles.note}>
      <Icon name={icon} size={18} color={Colors.pumpkinInk} />
      <AppText variant="caption" color={Colors.pumpkinInk} style={styles.flex}>
        {text}
      </AppText>
    </View>
  );
}

function ControlButton({ icon, label, onPress, primary, disabled }: { icon: IconName; label: string; onPress: () => void; primary?: boolean; disabled?: boolean }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.control, primary && styles.controlPrimary, disabled && styles.controlDisabled, pressed && styles.pressed]}
    >
      <Icon name={icon} size={20} color={primary ? Colors.surface : Colors.cherry} />
      <AppText variant="buttonSmall" color={primary ? Colors.surface : Colors.cherry} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.8} style={styles.shrink}>
        {label}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  shrink: { flexShrink: 1 },
  pressed: { opacity: 0.7 },
  panel: { gap: 10, paddingBottom: 2 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 64 },
  meta: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 2 },
  orbWrap: { width: 64, height: 64, alignItems: 'center', justifyContent: 'center' },
  ring: { position: 'absolute', width: 56, height: 56, borderRadius: 28, backgroundColor: Colors.sageTint, borderWidth: 2, borderColor: Colors.sage },
  badge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 12, maxWidth: 140, flexShrink: 0 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  track: { height: 6, borderRadius: 3, backgroundColor: Colors.cherryMist, overflow: 'hidden' },
  fill: { width: '100%', height: '100%', borderRadius: 3, backgroundColor: Colors.cherry, transformOrigin: 'left' },
  // A MINIMUM height (3 heading lines + the English line), not a fixed one:
  // the panel keeps one height for almost every question, and grows for a
  // long one or a large system text size instead of clipping it.
  questionCard: { gap: 4, minHeight: 136, justifyContent: 'center', paddingHorizontal: 14, paddingVertical: 12, borderRadius: 18, backgroundColor: Colors.surface, borderWidth: 1, borderColor: Colors.bandLine },
  noShrink: { flexShrink: 0 },
  questionCardListening: { borderColor: Colors.sage, borderWidth: 2 },
  controls: { flexDirection: 'row', gap: 10 },
  control: {
    flex: 1,
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    paddingHorizontal: 12,
    borderRadius: 24,
    borderWidth: 1.5,
    borderColor: Colors.cherry,
    backgroundColor: Colors.surface,
  },
  controlPrimary: { backgroundColor: Colors.cherry },
  controlDisabled: { opacity: 0.4 },
  note: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10, borderRadius: 14, backgroundColor: Colors.orangeTint },
  tap: { minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, borderRadius: 24, backgroundColor: Colors.sage },
  alert: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: 16, borderWidth: 2, borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  undo: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.cherry, backgroundColor: Colors.surface },
  thread: { gap: 10 },
  row: { flexDirection: 'row', alignItems: 'flex-end', gap: 8, maxWidth: '90%' },
  rowMine: { alignSelf: 'flex-end' },
  avatar: { width: 26, height: 26, borderRadius: 13, backgroundColor: Colors.orangeTint, alignItems: 'center', justifyContent: 'center' },
  avatarDanger: { backgroundColor: Colors.cherry },
  bubble: { flexShrink: 1, gap: 3, paddingHorizontal: 14, paddingVertical: 10, borderRadius: 18 },
  bubbleSakhi: { backgroundColor: Colors.shell, borderBottomLeftRadius: 6 },
  bubbleMine: { backgroundColor: Colors.cherryMist, borderBottomRightRadius: 6 },
  bubbleDanger: { borderWidth: 2, borderColor: Colors.cherry },
  bubbleSoft: { opacity: 0.85 },
  intro: { alignItems: 'center', gap: 16, paddingTop: 4 },
  langPick: { alignSelf: 'stretch', gap: 8 },
  liveNote: { alignSelf: 'stretch', flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 4 },
  steps: { alignSelf: 'stretch', gap: 2, padding: 6, borderRadius: 20, backgroundColor: Colors.orangeTint },
  stepRow: { flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 52, paddingHorizontal: 10 },
  stepNum: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  tools: { borderRadius: Layout.radius, borderWidth: 1.5, borderStyle: 'dashed', borderColor: Colors.outline },
  toolsHead: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 14 },
  toolsBody: { gap: 10, paddingHorizontal: 14, paddingBottom: 14 },
});
