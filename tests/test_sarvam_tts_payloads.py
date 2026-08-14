import unittest

from sarvam_wrappers import (
    SarvamTTS,
    _normalize_tts_text,
    _payload_shape,
    _sarvam_tts_config_payload,
    _sarvam_tts_flush_payload,
    _sarvam_tts_text_payload,
)


class SarvamTTSPayloadTests(unittest.TestCase):
    def make_tts(self, **overrides):
        params = {
            "api_key": "test-key",
            "model": "bulbul:v3",
            "speaker": "rohan",
            "target_language_code": "en-IN",
            "pace": 1.0,
            "min_buffer_size": 5,
            "max_chunk_length": 160,
            "output_audio_codec": "wav",
        }
        params.update(overrides)
        return SarvamTTS(**params)

    def test_config_payload_is_dictionary_without_min_buffer_size(self):
        payload = _sarvam_tts_config_payload(self.make_tts())
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["type"], "config")
        self.assertIsInstance(payload["data"], dict)
        self.assertEqual(payload["data"]["speaker"], "rohan")
        self.assertEqual(payload["data"]["target_language_code"], "en-IN")
        self.assertNotIn("min_buffer_size", payload["data"])

    def test_plain_text_payload_shape(self):
        payload = _sarvam_tts_text_payload("I need to book an appointment.", max_chars=160)
        self.assertEqual(payload, {"type": "text", "data": {"text": "I need to book an appointment."}})
        self.assertEqual(_payload_shape(payload), {"type": "str", "data": {"text": "str"}})

    def test_streamed_tiny_tokens_join_to_valid_text(self):
        text = "".join(["Of", " course", "!", " May", " I", " help", "?"])
        payload = _sarvam_tts_text_payload(text, max_chars=160)
        self.assertEqual(payload["data"]["text"], "Of course! May I help?")
        self.assertIsInstance(payload["data"], dict)

    def test_empty_or_punctuation_only_input_is_rejected_locally(self):
        self.assertIsNone(_sarvam_tts_text_payload("", max_chars=160))
        self.assertIsNone(_sarvam_tts_text_payload("   ... !!! ", max_chars=160))

    def test_invalid_speaker_is_remapped_not_fatal(self):
        """An unusable speaker must NOT abort TTS construction.

        This previously asserted that an unknown speaker raised ValueError. On a
        live maternal-health call that behaviour is worse than the problem it
        guards: a stale voice name in dispatch metadata would fail the whole TTS
        build and the ASHA worker would hear nothing at all.

        resolve_sarvam_voice() now substitutes a valid speaker for the selected
        model and logs a warning, so a bad voice costs the wrong timbre rather
        than a silent call. The model/speaker sets do not overlap between
        bulbul:v2 and v3, so this path is easy to hit by accident.
        """
        tts_instance = self.make_tts(speaker="not-a-real-speaker")
        payload = _sarvam_tts_config_payload(tts_instance)
        speaker = payload["data"]["speaker"]
        self.assertNotEqual(speaker, "not-a-real-speaker")
        self.assertTrue(speaker)

    def test_speaker_is_paired_with_a_compatible_model(self):
        from sarvam_wrappers import SARVAM_V2_SPEAKERS, resolve_sarvam_voice

        # A v3 voice requested against v2 is a hard 400 from the API.
        model, speaker = resolve_sarvam_voice("bulbul:v2", "pooja")
        self.assertEqual(model, "bulbul:v2")
        self.assertIn(speaker, SARVAM_V2_SPEAKERS)

        # A valid pairing is passed through untouched.
        self.assertEqual(resolve_sarvam_voice("bulbul:v2", "vidya"), ("bulbul:v2", "vidya"))
        self.assertEqual(resolve_sarvam_voice("bulbul:v3", "pooja"), ("bulbul:v3", "pooja"))

    def test_422_sanitized_shape_has_no_text_or_secret(self):
        payload = _sarvam_tts_text_payload("Patient phone 9-8-4-5.", max_chars=160)
        shape = _payload_shape(payload)
        self.assertEqual(shape, {"type": "str", "data": {"text": "str"}})
        self.assertNotIn("Patient", repr(shape))
        self.assertNotIn("test-key", repr(shape))

    def test_flush_payload_is_dictionary(self):
        payload = _sarvam_tts_flush_payload()
        self.assertEqual(payload, {"type": "flush", "data": {}})
        self.assertIsInstance(payload["data"], dict)

    def test_text_is_capped_to_max_chunk_length(self):
        text = _normalize_tts_text("a" * 200, max_chars=80)
        self.assertEqual(len(text), 80)


if __name__ == "__main__":
    unittest.main()
