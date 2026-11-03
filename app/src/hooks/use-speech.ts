import * as Speech from 'expo-speech';
import { useCallback, useEffect, useRef, useState } from 'react';

import { LANGUAGES, type Lang } from '@/i18n/strings';

// Spoken previews with the phone's own text-to-speech (works in Expo Go).
// One utterance at a time across the app: starting a new one stops the last.
// `available` is false when the phone has no voice for the language, so the
// UI can say so instead of reading Kannada in an English voice.

let voicesPromise: Promise<Speech.Voice[]> | null = null;
function voices() {
  voicesPromise ??= Speech.getAvailableVoicesAsync().catch(() => []);
  return voicesPromise;
}

export function useSpeech(lang: Lang) {
  const [speakingId, setSpeakingId] = useState<string | null>(null);
  const [available, setAvailable] = useState(true);
  const tag = LANGUAGES.find((l) => l.id === lang)?.speech ?? 'en-IN';
  const current = useRef<string | null>(null);

  useEffect(() => {
    let alive = true;
    voices().then((list) => {
      if (!alive) return;
      const prefix = tag.slice(0, 2).toLowerCase();
      // An empty list means the platform will not tell us; try anyway.
      setAvailable(list.length === 0 || list.some((v) => v.language.toLowerCase().startsWith(prefix)));
    });
    return () => {
      alive = false;
    };
  }, [tag]);

  // Never keep talking after the screen goes away or the language changes.
  useEffect(() => {
    return () => {
      Speech.stop().catch(() => {});
    };
  }, [tag]);

  const stop = useCallback(() => {
    current.current = null;
    setSpeakingId(null);
    Speech.stop().catch(() => {});
  }, []);

  const speak = useCallback(
    (id: string, text: string) => {
      if (current.current === id) {
        stop();
        return;
      }
      Speech.stop().catch(() => {});
      current.current = id;
      setSpeakingId(id);
      const done = () => {
        if (current.current === id) {
          current.current = null;
          setSpeakingId(null);
        }
      };
      Speech.speak(text, { language: tag, rate: 0.92, onDone: done, onStopped: done, onError: done });
    },
    [stop, tag],
  );

  return { speak, stop, speakingId, available };
}
