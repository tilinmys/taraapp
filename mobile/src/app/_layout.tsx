import { Stack } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { Brand } from '@/constants/brand';

// The native splash (same Botanical Midnight colour, see app.json) stays up
// until the animated splash in index.tsx has painted its first frame.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: Brand.botanicalMidnight }}>
      <SafeAreaProvider>
        <StatusBar style="light" />
        <Stack
          screenOptions={{
            headerShown: false,
            // Each screen paints its own canvas; no default white flash between them.
            contentStyle: { backgroundColor: Brand.alabaster },
            animation: 'fade',
          }}
        >
          <Stack.Screen name="index" options={{ contentStyle: { backgroundColor: Brand.botanicalMidnight } }} />
          <Stack.Screen name="welcome" options={{ animation: 'none', gestureEnabled: false }} />
        </Stack>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}
