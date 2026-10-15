# Proton: SteamUtils011 compatibility repair

## Symptom and cause

Enshrouded Dedicated Server Steam build `25936466` exits during Steamworks
initialization with `Version Mismatch` / `No SteamUtils011` when using the
published `1.7.2-proton` image. The same dependency mismatch is present in the
Proton Dockerfile at upstream revision
`1923c29877efe43eb6df8c82043e94d490590dd6`.

The Windows Steam client distributed with the game contains `SteamUtils011`,
but GE-Proton10-30's `lsteamclient.so` and the image's build-time Linux SDK
client do not. Updating the game through SteamCMD does not replace Proton or
refresh the SDK libraries already linked into the game user's home.

## Changes

- Upgrade GE-Proton to 11-9 and use its architecture-qualified release filename.
- Fail on HTTP download errors and verify the archive against the publisher's
  pinned SHA-512 checksum before extraction.
- Install `libvulkan1`, needed by the Proton 11 launcher on this headless server.
- Self-update SteamCMD before creating the game user's SDK hard links.
- Fail the build if either required 64-bit library lacks `SteamUtils011`.

The checksum protects archive integrity against the pinned publisher value;
it is not an independent signature verification. SteamCMD and APT dependencies
still use their upstream update channels, so the source commit and resulting
local image ID should be recorded for each build.

## Build and packaging check

From the repository root:

```sh
docker build --pull -f dockerfiles/proton.Dockerfile \
  -t localhost/enshrouded:source-proton11-9 .
docker run --rm --network none --entrypoint python3 \
  localhost/enshrouded:source-proton11-9 \
  /usr/local/etc/enshrouded/check-steam-interfaces.py
```

Both library checks must pass. This string-presence guard is necessary but not
sufficient: also verify a real game process, successful Steam connection,
`Host_Online`, a real A2S response, and clean stop/start with saved state.
A game-client join is a separate acceptance test.

## Safe trial and prefix migration

Back up private configuration before changing images. Test with a separate data
tree, fresh Proton prefix, separate container/project, and unused UDP query port.
Do not share writable game or save directories between production and a trial.
Keep secrets out of the checkout and build context; do not copy `.env` into it.
Use the site's production Compose configuration with a local image override,
not the repository's example Compose file verbatim (its device mounts may not
apply to your host).

Older prefixes may retain obsolete Steam integration. If necessary, stop the
container and **rename** `server/steamapps/compatdata/2278520` before starting
with Proton 11, preserving the old prefix for rollback. Never delete world
saves as a workaround for a Steam interface mismatch.

## Separate save-format backup incompatibility

During the fresh-world trial, the game wrote `world0.ksd` and checkpoint files
such as `world0.ksd.backup-8`. The existing backup wrapper still requires
`3ad85aea-index`, so a manual native backup attempt logged
`Save file not found - aborting backup` and exited successfully without making
an archive. This image repair does **not** fix that separate backup defect.
Do not assume the existing backup cron protects worlds created by this build;
verify a compatible, consistent save-backup method before relying on it.

## Following upstream

Keep local repairs on a branch. Fetch and review upstream changes before
rebasing or rebuilding; do not automatically replace a running server just
because `main` moved. Record the selected source revision and built image ID,
then repeat the isolated runtime checks before promotion.
