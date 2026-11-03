import { useEffect, useRef } from 'react';
import { StyleSheet, View } from 'react-native';
import { WebView } from 'react-native-webview';

import { gateway } from '@/voice/gateway';
import { pageScript, type LiveSession } from '@/voice/live';

// The media layer of Live Sakhi on phones: an invisible WebView running the
// gateway's WebRTC page (sakhi_live/page.html, livekit-client). Expo Go has
// no native WebRTC module, but the system WebView (WKWebView / Android
// System WebView) does: real WebRTC, echo cancellation and all, no dev build.
//   app -> page   injectJavaScript("window.sakhi.<cmd>(...)")
//   page -> app   window.ReactNativeWebView.postMessage(JSON)
// Mic permission: the app already holds it (expo-audio asked on the intro);
// iOS: mediaCapturePermissionGrantType="grant", Android: the WebView grants
// RECORD_AUDIO capture to the page when the app has it.

// The package types default P to `undefined` (props & undefined = never under
// strict TS): instantiate with `object`.
type Web = WebView<object>;

export function LiveBridge({ session }: { session: LiveSession }) {
  const ref = useRef<Web>(null);

  useEffect(() => {
    session.attach((c) => ref.current?.injectJavaScript(pageScript(c)));
    return () => session.attach(null);
  }, [session]);

  if (!gateway.livePageUrl) return null;
  return (
    <View style={styles.host} pointerEvents="none" aria-hidden>
      <WebView<object>
        ref={ref}
        source={{ uri: gateway.livePageUrl }}
        originWhitelist={['https://*', 'http://*']}
        javaScriptEnabled
        mediaPlaybackRequiresUserAction={false}
        allowsInlineMediaPlayback
        mediaCapturePermissionGrantType="grant"
        webviewDebuggingEnabled={__DEV__}
        onMessage={(e) => session.onPageMessage(e.nativeEvent.data)}
        onError={(e) => session.onPageError(e.nativeEvent.description)}
        onHttpError={(e) => session.onPageError(`HTTP ${e.nativeEvent.statusCode}`)}
        style={styles.web}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  // Tiny and transparent but laid out: an off-screen WebView may be paused.
  host: { position: 'absolute', left: 0, bottom: 0, width: 2, height: 2, opacity: 0.01 },
  web: { width: 2, height: 2, backgroundColor: 'transparent' },
});
