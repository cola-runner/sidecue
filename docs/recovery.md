# Validation Status

## Implemented

- Native AppKit window, manual input, source management, context/evidence disclosure, copying, and pinning.
- macOS Speech integration with on-device recognition required by default.
- Local keyword retrieval over TXT, MD, PDF, and DOCX sources.
- Codex CLI generation in an ephemeral session and temporary working directory.
- Independent input and generation loops with a single latest-input pending slot.
- Previous cues retained during updates, and separate input/generation error handling.
- Shutdown of application-owned input resources and generation processes.
- Local app builds restricted to public code, icons, configuration, and fictional sample data.

## Automated Checks

Tests cover configuration, retrieval, generation adapters, speech callbacks, resource cleanup, window state, native controls, scheduled exits, and text-to-mock integration. Opt-in bundle tests rebuild the actual `.app`, then exercise repeated startup/shutdown, mock generation, console operation, help, and invalid arguments.

The native launcher formerly held an Objective-C autorelease pool across Python finalization. Releasing UI objects afterward could call back into an already-stopped interpreter and trigger a segmentation fault. The bootstrap pool now ends before Python starts; the real bundle shutdown regression tests cover this boundary.

These checks do not verify real recognition quality, account/model availability, end-to-end latency, or long-meeting stability.

## Manual Acceptance

Use fictional data. Do not record or publish raw audio, transcripts, credentials, source paths, or device identities.

1. Preview the UI without a microphone or model:

```sh
./.venv/bin/python -m sidecue --preview-ui
```

2. Check real model generation using text, without microphone capture:

```sh
./scripts/run_macos_app.sh --asr-mode stdin --run-seconds 30 \
  --text 'What are the development constraints in the sample notes?'
```

3. When ready to authorize capture, check real recognition separately:

```sh
./scripts/check_mic.sh --check-seconds 15
```

4. With permissions granted and participant consent, run a bounded end-to-end check:

```sh
./scripts/run_macos_app.sh --run-seconds 45
```

Verify live input in Prompt details, relevant source snippets, real generated cues, continued input during generation, and process cleanup after closing. Report anonymous outcomes and timing, not original conversation content.

## Still Unverified

- Speech language resources, input devices, and permission-state compatibility across machines.
- Long sessions, sleep/wake transitions, sustained input, and resource use.
- Real speech-to-cue latency and cue quality.
- Source-update concurrency under sustained use.
- A standalone installer independent of the build machine's Python, with release signing and notarization.

Do not reset system permissions, edit permission databases, or stop system services to bypass authorization.
