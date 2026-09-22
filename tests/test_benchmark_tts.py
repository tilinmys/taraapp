import benchmark_tts


def test_benchmark_defaults_match_production_path():
    assert benchmark_tts.DEFAULT_MODEL == "bulbul:v2"
    assert benchmark_tts.DEFAULT_SPEAKER == "manisha"


def test_benchmark_reads_env_and_measures_clause_and_sentence():
    script = benchmark_tts.build_remote_script()

    assert 'os.getenv("SARVAM_TTS_MODEL")' in script
    assert 'os.getenv("SARVAM_SPEAKER")' in script
    assert '"short_first_clause"' in script
    assert '"full_sentence"' in script
    assert script.count("elapsed=") == 1
