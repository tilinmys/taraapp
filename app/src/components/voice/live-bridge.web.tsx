import { useEffect, useRef } from 'react';

import { gateway } from '@/voice/gateway';
import type { LiveSession } from '@/voice/live';

// The media layer of Live Sakhi on the web (Expo web, desktop testing): the
// same WebRTC page as the phones, in an invisible iframe. The Start tap in
// the app counts as the user gesture the browser needs for sound: "autoplay"
// and "microphone" are delegated to the frame.
//   app -> page   iframe.contentWindow.postMessage({sakhiCmd})
//   page -> app   window.parent.postMessage({sakhiLive})

export function LiveBridge({ session }: { session: LiveSession }) {
  const ref = useRef<HTMLIFrameElement>(null);

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== ref.current?.contentWindow || !e.data?.sakhiLive) return;
      session.onPageMessage(e.data.sakhiLive);
    };
    window.addEventListener('message', onMessage);
    session.attach((c) => ref.current?.contentWindow?.postMessage({ sakhiCmd: c }, '*'));
    return () => {
      window.removeEventListener('message', onMessage);
      session.attach(null);
    };
  }, [session]);

  if (!gateway.livePageUrl) return null;
  return (
    <iframe
      ref={ref}
      title="Sakhi live audio"
      src={gateway.livePageUrl}
      allow="microphone; autoplay"
      onError={() => session.onPageError('frame failed to load')}
      style={{ position: 'absolute', left: 0, bottom: 0, width: 2, height: 2, opacity: 0.01, border: 0, pointerEvents: 'none' }}
      aria-hidden
    />
  );
}
