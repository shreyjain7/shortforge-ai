# Changelog

## [0.2.1] - 2026-09-27

### New
- Dashboard "Get started" checklist; select several Shorts to upload privately, schedule or delete them at once.
- Version and update badge in the sidebar; "Studio" links for published videos.
- Clear instructions when the YouTube Data API isn't enabled or the Google account has no channel.

### Fixed
- Autopilot auto-upload no longer re-uploads a Short you re-render or edit by hand, and uploads each Short at most once.
- The window is resized to fit smaller / high-DPI laptop screens.
- Closing the app now fully stops the local engine.
- Channels that don't exist are re-checked daily instead of every 30 minutes.

## [0.2.0] - 2026-09-27

### New
- Downloadable Windows installer: first launch sets up the local AI engine automatically with a progress screen.
- Automatic updates from GitHub Releases, with download progress and release notes inside the app.
- One-click installs for FFmpeg and the Deno JavaScript runtime (no winget or Node.js needed).
- Action mode: videos with little speech (sports, POV, parkour) are clipped by motion, sound and editing rhythm,
  with titles written from what a local vision model sees.
- B-roll library (auto-tagged, matched to what is said) and music beds with ducking and beat-aligned punch-ins.
- Prompt-template overrides and editable ranking weights in Advanced mode.

### Fixed
- Uploads that fail no longer mark a finished Short as failed.
- Channels that don't exist fail immediately with a clear message instead of retrying as network errors.
- Rooftop / architectural footage is no longer mistaken for screen recordings (letterboxed layout).
- Audio could clip on noisy sources; a final limiter and progressive auto-repair fix it.
- Caption pages could flash for a fraction of a second; punch-ins could be too frequent on short clips.
- "Clean up now" in Settings called an endpoint that did not exist.
- Directly pasted videos were wrongly filtered by the channel age/duration rules.

## [0.1.0] - 2026-09-27

- First version: discovery for any public channel, transcription, 8-pass clip finder, 9:16 reframing, animated
  captions, NVENC rendering, quality control, YouTube publishing, analytics and learning, desktop app.
