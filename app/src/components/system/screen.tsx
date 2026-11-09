import { useFocusEffect } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useEffect, useRef, type MutableRefObject, type ReactNode } from 'react';
import { Pressable, StyleSheet, useWindowDimensions, View, type LayoutChangeEvent } from 'react-native';
import Animated, {
  Easing,
  Extrapolation,
  interpolate,
  useAnimatedRef,
  useAnimatedScrollHandler,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { StickyCta } from '@/components/onboarding/sticky-cta';
import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Nav, Physics, Signature, useContentWidth } from '@/constants/theme';
import type { TypeRole } from '@/constants/typography';
import type { Cta } from '@/features/onboarding/context';

// THE screen frame. Every workspace screen uses it, so navigation, spacing
// and safe areas are identical everywhere.
//
//   Nav bar     52 pt + the top safe area (notch / Dynamic Island / status
//               bar). Left: a LABELLED back button ("‹ Mothers"), never a
//               bare arrow. Right: one icon action with a 44 pt target.
//               A hairline and the compact title fade in once scrolled.
//   Large title Below the nav bar, inside the scroll: wraps to as many lines
//               as needed and is never truncated. Scrolling past it morphs
//               it into the nav bar title (scroll-aware sticky header).
//   Column      Centred, 520 pt phones / 720 pt tablets, 24 pt gutter, plus
//               the left/right safe areas in landscape.
//   CTA         The single sticky 56 pt primary action (+ bottom safe area).
//   Overlay     Sheets and modals, drawn above everything including the CTA.
//   sheetProgress  When a sheet is open the screen scales to 0.96 behind it.
//   band        Workspace homes: the nav bar, title and `accessory` sit on a
//               full-width Colors.band strip closed by a Colors.bandLine hairline, so the
//               header reads as one block, clearly apart from the content.
//   stickyAccessory  With `band`: the accessory (e.g. a section jump bar)
//               pins under the nav bar while the content scrolls.
//   jumpRef     Filled with a function that smooth-scrolls to a y offset
//               measured inside the content column (section jump bars).
//   inTabs      Inside the bottom tab bar: no sticky CTA spacer (the bar
//               owns the bottom safe area).
//   hideTitle   No large title: the nav bar shows the compact title from the
//               start (a conversation, whose sticky panel would otherwise
//               cover a half-scrolled, half-faded large title).

/** `art` (right button only) shows a clay icon instead of the line icon. */
/** `badge` (right buttons) shows a small count, e.g. unread notifications. */
export type NavAction = { label: string; onPress: () => void; icon?: IconName; art?: AppIconName; badge?: number };

type Props = {
  title: string;
  /** Type role of the large title (mother screens use the handwritten 'greeting'). */
  titleVariant?: TypeRole;
  /** Small line above the title (workspace, place, step). */
  eyebrow?: string;
  subtitle?: string;
  /** Short title for the nav bar once the large title scrolls away. */
  compactTitle?: string;
  /** Extra element beside the compact nav title (e.g. a risk pill). */
  compactAccessory?: ReactNode;
  back?: NavAction;
  right?: NavAction;
  /** A second right-hand button, left of `right` (e.g. a notifications bell). */
  rightExtra?: NavAction;
  /** Content under the title, above the main column (progress bars etc.). */
  accessory?: ReactNode;
  cta?: Cta | null;
  overlay?: ReactNode;
  sheetProgress?: SharedValue<number>;
  children: ReactNode;
  gap?: number;
  /** Keep the newest content in view (conversations). */
  followContent?: boolean;
  band?: boolean;
  stickyAccessory?: boolean;
  inTabs?: boolean;
  jumpRef?: MutableRefObject<((y: number) => void) | null>;
  hideTitle?: boolean;
};

export function NavButton({ action, align }: { action: NavAction; align: 'left' | 'right' }) {
  const isBack = align === 'left';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={action.badge ? `${action.label}, ${action.badge} new` : action.label}
      onPress={action.onPress}
      hitSlop={6}
      style={({ pressed }) => [styles.navBtn, isBack ? styles.navBtnLeft : styles.navBtnRight, pressed && styles.pressed]}
    >
      {isBack && <Icon name={action.icon ?? 'back'} size={22} color={Colors.cherry} weight="bold" />}
      {isBack ? (
        <AppText variant="buttonSmall" color={Colors.cherry} numberOfLines={1} style={styles.navLabel}>
          {action.label}
        </AppText>
      ) : action.art ? (
        <AppIcon name={action.art} size={32} />
      ) : (
        <Icon name={action.icon ?? 'profile'} size={26} color={Colors.cherry} />
      )}
      {!isBack && !!action.badge && (
        <View style={styles.badge} aria-hidden>
          <AppText variant="overline" color={Colors.onCherry}>
            {action.badge > 9 ? '9+' : String(action.badge)}
          </AppText>
        </View>
      )}
    </Pressable>
  );
}

/** How close to the bottom (pt) still counts as "following" the conversation. */
const FOLLOW_SLACK = 160;

export function Screen({
  title,
  titleVariant = 'title',
  eyebrow,
  subtitle,
  compactTitle,
  compactAccessory,
  back,
  right,
  rightExtra,
  accessory,
  cta,
  overlay,
  sheetProgress,
  children,
  gap = 20,
  followContent = false,
  band = false,
  stickyAccessory = false,
  inTabs = false,
  jumpRef,
  hideTitle = false,
}: Props) {
  const insets = useSafeAreaInsets();
  const { maxWidth, gutter } = useContentWidth();
  const scrollY = useSharedValue(0);
  const scrollRef = useAnimatedRef<Animated.ScrollView>();
  const idle = useSharedValue(0);
  const headerH = useRef(0);
  const bodyPad = band ? 20 : gap;
  useEffect(() => {
    if (!jumpRef) return;
    jumpRef.current = (y: number) =>
      scrollRef.current?.scrollTo({ y: Math.max(0, headerH.current + bodyPad + y - 12), animated: true });
    return () => {
      jumpRef.current = null;
    };
  }, [bodyPad, jumpRef, scrollRef]);
  const sheet = sheetProgress ?? idle;

  // Tab switch (motion spec #1, calm version): the tab navigator itself has
  // no transition (its animated scenes could detach and go blank), so the
  // focused tab glides in 8 pt from the right over 180 ms ease-out: no
  // spring, no overshoot. Opacity never drops below 0.85: the screen can
  // never be invisible.
  const reduceMotion = useReducedMotion();
  const enter = useSharedValue(1);
  useFocusEffect(
    useCallback(() => {
      if (!inTabs || reduceMotion) return;
      enter.set(0);
      enter.set(
        withTiming(1, { duration: Nav.tab, easing: Easing.out(Easing.cubic) }, (finished) => {
          if (!finished) enter.set(1);
        }),
      );
      return () => enter.set(1);
    }, [enter, inTabs, reduceMotion]),
  );

  // followContent = chat behaviour: new content scrolls into view ONLY while
  // the reader is at (or near) the bottom. "Pinned" is decided from real
  // scroll positions (UI thread), never at the moment content grows: a
  // smooth scroll still in flight would otherwise look like "scrolled up"
  // and following would stop (measured: 477 pt short of the bottom). When
  // pinned, the jump to the end is instant, so no animation can race the
  // next line. Someone who scrolled up to read stays exactly where they are.
  // Pinned breaks ONLY on an upward scroll (what a reader going back does);
  // layout and growth events never break it. It re-pins at the bottom.
  const viewportH = useSharedValue(0);
  const contentH = useSharedValue(0);
  const pinned = useSharedValue(true);
  const onScroll = useAnimatedScrollHandler((e) => {
    const y = e.contentOffset.y;
    if (contentH.get() - (y + viewportH.get()) < FOLLOW_SLACK) pinned.set(true);
    else if (y < scrollY.get() - 2) pinned.set(false);
    scrollY.set(y);
  });
  // Measured on the content itself (onLayout fires on every platform; the
  // animated ScrollView's onContentSizeChange never fired on the web).
  const bodyTop = useRef(0);
  const onBodyLayout = (e: LayoutChangeEvent) => {
    const h = bodyTop.current + e.nativeEvent.layout.height;
    if (Math.abs(h - contentH.get()) < 1) return;
    contentH.set(h);
    if (pinned.get()) scrollRef.current?.scrollToEnd({ animated: false });
  };
  // A conversation view starting (or restarting) follows from the start.
  useEffect(() => {
    if (followContent) pinned.set(true);
  }, [followContent, pinned]);
  const navTitleStyle = useAnimatedStyle(() => {
    const t = interpolate(scrollY.get(), [Signature.titleCollapse * 0.5, Signature.titleCollapse], [0, 1], Extrapolation.CLAMP);
    return { opacity: t, transform: [{ translateY: (1 - t) * 8 }] };
  });
  const hairlineStyle = useAnimatedStyle(() => ({
    opacity: interpolate(scrollY.get(), [0, 12], [0, 1], Extrapolation.CLAMP),
  }));
  const bigTitleStyle = useAnimatedStyle(() => ({
    opacity: interpolate(scrollY.get(), [0, Signature.titleCollapse], [1, 0], Extrapolation.CLAMP),
  }));
  const backStyle = useAnimatedStyle(() => ({
    opacity: 0.85 + 0.15 * enter.get(),
    transform: [{ translateX: (1 - enter.get()) * Nav.tabTravel }, { scale: 1 - (1 - Physics.sheetBackScale) * sheet.get() }],
    borderRadius: 20 * sheet.get(),
  }));

  const canvasColor = band ? Colors.band : Colors.porcelain;
  const sticky = band && stickyAccessory && !!accessory;
  const sidePad = {
    paddingLeft: Math.max(gutter, insets.left + 16),
    paddingRight: Math.max(gutter, insets.right + 16),
  };
  // A width in points, not '100%': Android's sticky header wrapper has no
  // width of its own, so a percentage there collapses to the content's
  // natural width and a long line pushed the panel off both screen edges.
  const { width: winW } = useWindowDimensions();
  const colW = Math.min(maxWidth, winW - sidePad.paddingLeft - sidePad.paddingRight);

  return (
    <View style={styles.canvas}>
      <StatusBar style="dark" />
      <Animated.View style={[styles.flex, styles.back, backStyle]}>
        {/* Nav bar */}
        <View style={[styles.nav, { paddingTop: insets.top, backgroundColor: canvasColor }]}>
          <View style={[styles.navRow, { paddingLeft: Math.max(8, insets.left + 4), paddingRight: Math.max(8, insets.right + 4) }]}>
            <View style={styles.navSide}>{back && <NavButton action={back} align="left" />}</View>
            <Animated.View style={[styles.navCenter, hideTitle ? styles.shown : navTitleStyle]} aria-hidden={!hideTitle}>
              <AppText variant="label" numberOfLines={1} style={styles.navTitle}>
                {compactTitle ?? title}
              </AppText>
              {compactAccessory}
            </Animated.View>
            <View style={[styles.navSide, styles.navSideRight]}>
              {rightExtra && <NavButton action={rightExtra} align="right" />}
              {right && <NavButton action={right} align="right" />}
            </View>
          </View>
          <Animated.View style={[styles.hairline, band && styles.bandHairline, hairlineStyle]} />
        </View>

        {/* Content */}
        <Animated.ScrollView
          ref={scrollRef}
          onLayout={(e) => {
            viewportH.set(e.nativeEvent.layout.height);
          }}
          onScroll={onScroll}
          scrollEventThrottle={16}
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={styles.scroll}
          stickyHeaderIndices={sticky ? [1] : undefined}
        >
          {/* Header block: title (+ accessory); on a band when `band`. */}
          <View
            style={hideTitle ? styles.gone : [styles.headerBlock, sidePad, band && styles.band, sticky && styles.bandOpen]}
            onLayout={(e) => {
              headerH.current = e.nativeEvent.layout.height;
            }}
          >
            <View style={[styles.column, { width: colW, gap: 16 }]}>
              <Animated.View style={[styles.titleBlock, bigTitleStyle]}>
                {!!eyebrow && (
                  <AppText variant="caption" color={Colors.inkMuted}>
                    {eyebrow}
                  </AppText>
                )}
                <AppText variant={titleVariant} accessibilityRole="header">
                  {title}
                </AppText>
                {!!subtitle && (
                  <AppText variant="body" color={Colors.inkMuted}>
                    {subtitle}
                  </AppText>
                )}
              </Animated.View>
              {band && !sticky && accessory}
            </View>
          </View>
          {sticky && (
            <View style={[styles.headerBlock, sidePad, styles.band, styles.stickyBar, hideTitle && styles.stickyTop, { width: winW }]}>
              <View style={[styles.column, { width: colW }]}>{accessory}</View>
            </View>
          )}
          <View
            style={[styles.body, sidePad, { paddingTop: bodyPad }]}
            onLayout={
              followContent
                ? (e) => {
                    bodyTop.current = e.nativeEvent.layout.y;
                    onBodyLayout(e);
                  }
                : undefined
            }
          >
            <View style={[styles.column, { width: colW, gap }]}>
              {!band && accessory}
              {children}
            </View>
          </View>
        </Animated.ScrollView>
      </Animated.View>

      {cta ? <StickyCta cta={cta} inTabs={inTabs} /> : inTabs ? null : <View style={{ height: Math.max(insets.bottom, 12) }} />}
      {overlay}
    </View>
  );
}

const styles = StyleSheet.create({
  canvas: { flex: 1, backgroundColor: Colors.porcelain, overflow: 'hidden' }, // parked sheets never extend the page
  flex: { flex: 1 },
  back: { overflow: 'hidden', backgroundColor: Colors.porcelain },
  nav: { backgroundColor: Colors.porcelain, zIndex: 2 },
  navRow: { height: 52, flexDirection: 'row', alignItems: 'center' },
  navSide: { flex: 1, flexDirection: 'row', alignItems: 'center', minWidth: 0 },
  navSideRight: { justifyContent: 'flex-end' },
  navCenter: { flexDirection: 'row', alignItems: 'center', gap: 8, maxWidth: '56%' },
  navTitle: { flexShrink: 1 },
  navBtn: { minHeight: 44, minWidth: 44, flexDirection: 'row', alignItems: 'center', borderRadius: 22 },
  navBtnLeft: { paddingRight: 10, gap: 2, maxWidth: '100%' },
  navBtnRight: { justifyContent: 'center' },
  navLabel: { flexShrink: 1 },
  pressed: { opacity: 0.6 },
  badge: { position: 'absolute', top: 4, right: 2, minWidth: 18, height: 18, paddingHorizontal: 4, borderRadius: 9, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  hairline: { height: StyleSheet.hairlineWidth, backgroundColor: Colors.hairline },
  scroll: { flexGrow: 1, paddingBottom: 28 },
  headerBlock: { alignItems: 'center', paddingTop: 4 },
  band: { backgroundColor: Colors.band, paddingBottom: 20, borderBottomWidth: 1, borderBottomColor: Colors.bandLine },
  bandHairline: { backgroundColor: Colors.bandLine },
  bandOpen: { paddingBottom: 12, borderBottomWidth: 0 },
  stickyBar: { paddingTop: 0, paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: Colors.bandLine },
  body: { alignItems: 'center' },
  column: { width: '100%' },
  titleBlock: { gap: 4 },
  shown: { opacity: 1 },
  stickyTop: { paddingTop: 10 },
  // Kept mounted (stickyHeaderIndices counts it) but takes no room.
  gone: { height: 0, overflow: 'hidden' },
});
