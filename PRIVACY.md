# Privacy

## Data Flow

macOS Speech transcribes microphone input. On-device recognition is required by default. Explicitly setting `asr.on_device = false` allows Apple online recognition. The application does not intentionally save raw audio.

Sources are parsed and searched locally. The Codex backend receives the current input, retrieved text snippets, and source filenames. This is not an end-to-end offline application, and local settings do not replace the model provider's data-retention controls.

Codex manages authentication through the user's existing client. Sidecue does not copy login files or require credentials in the repository. It removes `OPENAI_API_KEY` and `CODEX_API_KEY` from the child environment, requires ChatGPT login, and starts an ephemeral session in a temporary directory. Temporary input/output handling and `--ephemeral` do not imply zero server-side retention.

UI preview mode creates neither a speech source nor a real generation client. Its source picker changes only a sample list and does not parse selected files.

## Local Records

- Normal application logs contain states, character counts, timing, and exception categories, not input, cue, or source text.
- Native logs are appended to `~/Library/Logs/Sidecue/app.log`. System and third-party diagnostics may still include sensitive details; inspect them before sharing.
- Console mode, full generation context, clipboard content, and screen recordings can contain conversation data.
- Logs from earlier versions are not removed or rewritten automatically and may contain raw text.
- Sources and local configuration are not uploaded to the Git repository, but selected source content may still be sent to the model during generation.

## Publication Boundaries

Git ignores private source files, local configuration, credentials, logs, recordings, screenshots, virtual environments, and build artifacts. Ignore rules do not remove files that were already tracked or erase older commits.

The app builder copies only public code, UI assets, the sample configuration, and the designated fictional source. The resulting app still depends on machine-specific runtime paths and is not a standalone release artifact.

The README image is a deliberately prepared UI preview with fictional text, not a capture of a real conversation. Other screenshots must be reviewed before publication.

`scripts/check_public_tree.py --history` is an offline pattern scan of candidate files and locally reachable commits. It reports locations and rule names rather than matched values. It cannot detect every credential, identify all sensitive natural-language content, inspect image content, or cover remote history that has not been fetched.

Review author metadata, commit messages, attachments, screenshots, release files, and CI output before making them public. Obtain appropriate permission before capturing other people's speech or sending their information to a model service.
