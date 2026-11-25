# Frontbit Link No-ADB Live FAQ Bot

This folder contains the Frontbit Link/WASAPI version of the FAQ bot. It shares
the FAQ matcher and CABLE voice implementation in `AIPhoneVoiceBot`.

## Goal

Use Frontbit Link's Windows Bluetooth/HFP call audio instead of Android/scrcpy/ADB capture:

```text
Caller -> Frontbit Link Windows output -> WASAPI loopback -> STT -> FAQ
FAQ answer -> CABLE Input -> CABLE Output -> Frontbit Link microphone -> caller
```

The default capture endpoint is:

```text
Speakers (Realtek(R) Audio) [Loopback]
```

Device indices are not stored because Windows may renumber them. Selection is
performed by device name.

## Safe test order

1. Stop the production call bot process so two bots do not answer together.
2. Run `Start 1 - Monitor Only (No Echo Risk).cmd`.
3. Receive a call through Frontbit Link and speak FAQ questions from the remote
   phone. This stage never sends bot audio into the call.
4. Inspect `status.json` and the newest file in `logs`.
5. Continue only if caller speech is captured accurately from the loopback.
6. Run `Start 2 - FAQ Voice To Caller.cmd` for the full route.

Unknown or low-confidence text is logged and ignored. The production FAQ table
and threshold of 0.84 are preserved.

## Low-latency behavior

- Audio is captured in 100 ms blocks.
- Speech onset requires two voiced blocks, which rejects short clicks.
- A live STT snapshot is queued after roughly 600 ms of voiced speech and then
  every 500 ms while the caller continues talking.
- A live answer is allowed only for an exact or contained FAQ phrase with a
  score of at least 0.95. Less certain speech waits for the final result.
- The final snapshot is emitted after 200 ms of silence instead of 400 ms.
- FAQ intent words recover common tiny-model variations such as `your price`,
  `good price`, `charges`, and `contact call`.
- Fixed FAQ answers are synthesized once during startup and retained as
  conditioned PCM. The persistent CABLE WASAPI output is also opened during
  startup, so cache hits avoid live TTS generation and device-open latency.
- New caller speech immediately cancels active bot playback. Recognition keeps
  running during bot speech, so the caller can interrupt and ask another FAQ.

Useful `status.json` fields include `partial_text`, `answered_live`,
`voice_startup_ms`, `barge_in`, `voice_cache`, and `stt_seconds`.

The first monitor-only real-call test captured three of four FAQ phrases exactly
with latest measured STT time `0.392s`. The first quiet phrase was clipped to
`you`, so the POC-only energy threshold was reduced from `0.003` to `0.0015`.

## Rollback

Stop this POC process and restart the production launcher:

```text
AIPhoneVoiceBot\Start Call Qwen Voice Bot To Caller.cmd
```

The project is version controlled, so the low-latency changes can be reverted
with Git if a device-specific regression is found.
