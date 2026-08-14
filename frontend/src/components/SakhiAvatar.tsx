"use client";

import React, { useRef, useEffect } from 'react';
import { useFrame } from '@react-three/fiber';
import { useGLTF } from '@react-three/drei';
import * as THREE from 'three';

export function SakhiModel({ audioTrack }: { audioTrack?: MediaStreamTrack | null }) {
  // Preload the specific model we downloaded (RobotExpressive as fallback, or the specific Sakhi GLB)
  const { scene } = useGLTF('/models/sakhi_kannada.glb');
  const headMeshRef = useRef<THREE.SkinnedMesh | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const dataArrayRef = useRef<Uint8Array | null>(null);

  // 1. Connect LiveKit WebRTC Audio Track to Web Audio Analyser
  useEffect(() => {
    if (!audioTrack) return;
    
    // We use a small timeout to ensure the audio context is active (handled by the caller)
    const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
    
    // Fallback if browser requires gesture to start audio
    if (audioCtx.state === 'suspended') {
      audioCtx.resume();
    }

    const stream = new MediaStream([audioTrack]);
    const source = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 64; // Fast, low-overhead FFT for mobile performance
    source.connect(analyser);

    analyserRef.current = analyser;
    dataArrayRef.current = new Uint8Array(analyser.frequencyBinCount);

    return () => {
      source.disconnect();
      analyser.disconnect();
      audioCtx.close();
    };
  }, [audioTrack]);

  // Find head mesh with morph targets
  useEffect(() => {
    scene.traverse((child) => {
      if ((child as THREE.SkinnedMesh).isSkinnedMesh && child.morphTargetDictionary) {
        // If we find any skinned mesh with blendshapes, we store it for lip sync
        // You might need to specify the exact mesh name (e.g., 'Head' or 'Wolf3D_Avatar') if the model is complex.
        headMeshRef.current = child as THREE.SkinnedMesh;
      }
    });
  }, [scene]);

  // 2. Real-Time Frame-by-Frame Lip Sync Loop (60 FPS)
  useFrame((state) => {
    // Procedural Idle Breathing/Movement (always runs so she feels alive)
    const t = state.clock.getElapsedTime();
    const root = scene.getObjectByName('Root') || scene.getObjectByName('Spine') || scene;
    if (root) {
      // Gentle breathing wave
      root.rotation.x = Math.sin(t * 1.5) * 0.015;
    }

    // Blink occasionally
    if (headMeshRef.current && headMeshRef.current.morphTargetDictionary) {
      const dict = headMeshRef.current.morphTargetDictionary;
      const infl = headMeshRef.current.morphTargetInfluences!;
      
      const blinkTime = t % 4; // every 4 seconds roughly
      const blinkVal = blinkTime < 0.1 ? 1 : 0;
      
      if (dict['eyeBlinkLeft'] !== undefined) infl[dict['eyeBlinkLeft']] = THREE.MathUtils.lerp(infl[dict['eyeBlinkLeft']], blinkVal, 0.5);
      if (dict['eyeBlinkRight'] !== undefined) infl[dict['eyeBlinkRight']] = THREE.MathUtils.lerp(infl[dict['eyeBlinkRight']], blinkVal, 0.5);
      
      // Some models use 'Blink' instead of 'eyeBlinkLeft'
      if (dict['Blink'] !== undefined) infl[dict['Blink']] = THREE.MathUtils.lerp(infl[dict['Blink']], blinkVal, 0.5);
    }

    // Audio-reactive lip sync
    if (!headMeshRef.current || !analyserRef.current || !dataArrayRef.current) return;

    analyserRef.current.getByteFrequencyData(dataArrayRef.current);
    
    // Average specific frequency bins for Kannada phonetics
    const lowFreq = dataArrayRef.current[1] / 255.0;   // Vowels: Aa, A
    const midFreq = dataArrayRef.current[4] / 255.0;   // Vowels: Ee, I
    const highFreq = dataArrayRef.current[8] / 255.0;  // Vowels: Oo, U

    // Overall volume to detect silence
    let sum = 0;
    for (let i = 0; i < dataArrayRef.current.length; i++) sum += dataArrayRef.current[i];
    const volume = sum / dataArrayRef.current.length / 255.0;

    const dict = headMeshRef.current.morphTargetDictionary!;
    const infl = headMeshRef.current.morphTargetInfluences!;

    // Smooth Lerp to avoid mouth jitter/spasms
    const lerp = THREE.MathUtils.lerp;
    
    // Noise Gate & Silence Decay: If volume is < 0.04, smooth everything to 0
    const isActive = volume > 0.04;
    
    const targetLow = isActive ? lowFreq * 1.2 : 0;
    const targetMid = isActive ? midFreq * 0.8 : 0;
    const targetHigh = isActive ? highFreq * 0.9 : 0;

    // Standard ARKit / Oculus visemes
    if (dict['jawOpen'] !== undefined) infl[dict['jawOpen']] = lerp(infl[dict['jawOpen']], targetLow, 0.35);
    if (dict['viseme_aa'] !== undefined) infl[dict['viseme_aa']] = lerp(infl[dict['viseme_aa']], targetLow, 0.3);
    if (dict['viseme_I'] !== undefined) infl[dict['viseme_I']] = lerp(infl[dict['viseme_I']], targetMid, 0.3);
    if (dict['viseme_O'] !== undefined) infl[dict['viseme_O']] = lerp(infl[dict['viseme_O']], targetHigh, 0.3);
    if (dict['mouthPucker'] !== undefined) infl[dict['mouthPucker']] = lerp(infl[dict['mouthPucker']], targetHigh, 0.3);
    
    // Fallbacks for RobotExpressive or simpler models
    if (dict['MouthOpen'] !== undefined) infl[dict['MouthOpen']] = lerp(infl[dict['MouthOpen']], targetLow, 0.35);
  });

  return <primitive object={scene} position={[0, -1.8, 0]} scale={1.2} />;
}

// Preload the model to avoid delays in 3G areas
useGLTF.preload('/models/sakhi_kannada.glb');
