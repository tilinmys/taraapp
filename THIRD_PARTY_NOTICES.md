# Third-party notices

Material in this repository that the Sakhi (Tara) project did not create keeps
its own licence. It is **not** covered by the project's [MIT](LICENSE) or
[CC BY 4.0](LICENSE-CONTENT.md) licences.

## Bundled third-party files

| Material | Where | Licence |
| --- | --- | --- |
| LiveKit client SDK 2.20.0 | `sakhi_live/livekit-client-2.20.0.umd.js` | Apache-2.0 (`sakhi_live/LICENSE-livekit-client`) |
| Baloo 2 and Baloo Tamma 2 fonts (Ek Type) | `video/assets/fonts/`, `video/sos/assets/fonts/` | SIL Open Font License 1.1 |
| Satellite imagery in the globe flight | `video/assets/map/` | Esri World Imagery (sources: Esri, Maxar, Earthstar Geographics). Used under Esri's terms of use, with the on-screen credit; not open-licensed. |

Dependencies installed by `npm install` and `pip install -r requirements.txt`
are not vendored here; each keeps its own licence (see `app/package.json`,
`frontend/package.json`, `mobile/package.json`, `requirements.txt`).

## Pending a rights check

Made with third-party generative services. The project is confirming, against
each provider's terms, that it may publish them under CC BY 4.0. Until then
they are included for this project's use only.

| Material | Where | Made with |
| --- | --- | --- |
| App icons (clay style) | `app/assets/images/icons/` | Canva AI |
| The baby and story illustrations | `app/assets/images/art/` | Canva AI |
| Sakhi's recorded voice lines | `app/assets/voice/`, `assets/audio/prompts/`, `video/assets/voice/` | Gnani text-to-speech (voice "Kaveri") |
| Greeting, filler and comparison clips | `assets/audio/*.wav`, `assets/audio/compare/` | Sarvam text-to-speech |
