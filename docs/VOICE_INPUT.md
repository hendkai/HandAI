# Voice input

HandAI can turn speech into a prompt without sending microphone audio to an AI
API. The capture path is:

`microphone -> PipeWire/ALSA -> 16 kHz WAV -> whisper.cpp -> editable text -> tmux`

## Supported microphones

- Built-in or USB capture devices exposed by ALSA.
- Bluetooth headsets in HFP/HSP mode through BlueZ, PipeWire and WirePlumber.
  A2DP alone is playback-only; the headset must expose an HFP/HSP microphone.

Open **Voice Input -> Input Source** after connecting a headset. A Bluetooth
source normally appears with the headset name. Pairing and reconnecting are
available under **Voice Input -> Bluetooth Headsets**.

## Audio Center and microphone tests

Open **Settings -> Audio / Mic Test** to:

- choose the speaker, wired headphone or Bluetooth output;
- adjust output volume and microphone input level, including mute;
- play a generated speaker/headphone test tone;
- record a real microphone test and inspect average level, peak level,
  silence and clipping;
- play the microphone recording back or test it with local speech recognition;
- replay the generated `speaker-test.wav` and `mic-test.wav` test files.

The microphone test deliberately works without the Whisper model. This makes
it useful for diagnosing capture hardware, Bluetooth profiles and gain before
testing speech recognition. Bluetooth microphones require HFP/HSP; A2DP alone
still provides playback only.

## Local model

The multilingual `tiny-q5_1` whisper.cpp model is about 31 MB and supports
German. It is intentionally not embedded in the system image. Install it once
from **Voice Input -> Install Voice Model**; it is checksum-verified and stored
on the persistent `/data` partition.

Transcription is local and needs no API key, token or OAuth login. On the
quad-core Cortex-A53 it is expected to take longer than real time for longer
prompts, so short spoken prompts work best.

Environment overrides:

- `HANDAI_WHISPER_MODEL=/path/to/model.bin`
- `HANDAI_WHISPER_CLI=/path/to/whisper-cli`
