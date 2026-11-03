import { Baloo2_400Regular, Baloo2_500Medium, Baloo2_600SemiBold, Baloo2_700Bold } from '@expo-google-fonts/baloo-2';
import {
  BalooTamma2_400Regular,
  BalooTamma2_500Medium,
  BalooTamma2_600SemiBold,
  BalooTamma2_700Bold,
} from '@expo-google-fonts/baloo-tamma-2';
import { Parisienne_400Regular } from '@expo-google-fonts/parisienne';
import { Satisfy_400Regular } from '@expo-google-fonts/satisfy';
import { useAssets } from 'expo-asset';
import { useFonts } from 'expo-font';
import { Stack, type ErrorBoundaryProps } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { View } from 'react-native';

import { OfflineBanner } from '@/components/system/offline-banner';
import { StateView } from '@/components/system/state-view';

import { Brand } from '@/constants/brand';
import { Nav } from '@/constants/motion';
import { OnboardingProvider } from '@/features/onboarding/context';
import { preloadAppImages } from '@/lib/preload-images';

// The native splash (porcelain + the leaf mark, see app.json) stays up until
// the fonts are ready and the brand intro has drawn an identical first frame;
// brand-intro.tsx hides it. Text never renders in a fallback face first.
SplashScreen.preventAutoHideAsync();

// Only the weights the type scale uses (constants/typography.ts). Baloo 2
// covers Latin + Devanagari; Baloo Tamma 2 covers Kannada.
const FONTS = {
  Baloo2_400Regular,
  Baloo2_500Medium,
  Baloo2_600SemiBold,
  Baloo2_700Bold,
  BalooTamma2_400Regular,
  BalooTamma2_500Medium,
  BalooTamma2_600SemiBold,
  BalooTamma2_700Bold,
  Satisfy_400Regular,
  Parisienne_400Regular,
};

// Brand images decoded before the first frame, so the intro never waits on
// a network/disk fetch mid-animation (WebP: ~40 % smaller than the PNGs).
const PRELOAD = [
  require('@/assets/images/tara-logo.webp'),
  require('@/assets/images/logo-layers/tara-leaf.webp'),
  require('@/assets/images/logo-layers/tara-logo-t.webp'),
  require('@/assets/images/logo-layers/tara-logo-a1.webp'),
  require('@/assets/images/logo-layers/tara-logo-r.webp'),
  require('@/assets/images/logo-layers/tara-logo-a2.webp'),
  require('@/assets/images/logo-layers/tara-logo-figure.webp'),
];

/** Unknown crash in any screen: a calm recovery page instead of a red box. */
export function ErrorBoundary({ retry }: ErrorBoundaryProps) {
  return (
    <View style={{ flex: 1, justifyContent: 'center', paddingHorizontal: 24, backgroundColor: Brand.porcelain }}>
      <StateView
        title="Something went wrong"
        body="Nothing you saved is lost. Try again, and if it repeats, restart the app."
        action={{ label: 'Try again', onPress: retry }}
      />
    </View>
  );
}

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts(FONTS);
  const [assets, assetError] = useAssets(PRELOAD);
  const ready = (fontsLoaded || !!fontError) && (!!assets || !!assetError);

  // Once the intro can play, warm every other image in the background.
  useEffect(() => {
    if (ready) preloadAppImages();
  }, [ready]);

  // Hold on the native splash until fonts and brand images are ready
  // (or fail: then system fonts / lazy images, never a stuck splash).
  if (!ready) return null;

  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: Brand.porcelain }}>
      <SafeAreaProvider>
        <OnboardingProvider>
          <StatusBar style="dark" />
          <Stack
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: Brand.porcelain },
              animation: 'fade',
              animationDuration: Nav.push,
            }}
          >
            <Stack.Screen name="index" options={{ animation: 'none' }} />
            {/* Onboarding slides in from the right; inside it, steps use their own parallax. */}
            <Stack.Screen
              name="(onboarding)"
              options={{ animation: 'slide_from_right', animationDuration: Nav.push, gestureEnabled: false }}
            />
            {/* Role workspaces, entered with replace after the code check: no way "back" into sign-in. */}
            <Stack.Screen name="asha" options={{ animation: 'slide_from_right', gestureEnabled: false }} />
            <Stack.Screen name="mother" options={{ animation: 'slide_from_right', gestureEnabled: false }} />
            <Stack.Screen name="doctor" options={{ animation: 'slide_from_right', gestureEnabled: false }} />
            {/* Profile is shared by every role and slides in over the workspace. */}
            <Stack.Screen name="profile" options={{ animation: 'ios_from_right', gestureEnabled: true, fullScreenGestureEnabled: true }} />
            <Stack.Screen name="+not-found" options={{ animation: 'fade' }} />
          </Stack>
          <OfflineBanner />
        </OnboardingProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
