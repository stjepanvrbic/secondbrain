# Vault Sync

Your secondbrain vault is just a directory of Markdown files. To use it across multiple devices, you need to sync that directory between them somehow. This doc covers 5 options, ranked from easiest to hardest.

If you're not sure which to pick, **Obsidian Sync** is the easiest and most reliable. It costs $4/month but it just works.

---

## Option 1: Obsidian Sync (paid, easiest)

**Cost:** $4/month per user (Obsidian's first-party sync service)
**Best for:** anyone who can pay $4/month and wants to never think about sync

Setup:
1. Sign up at https://obsidian.md/sync
2. In Obsidian, Settings → Sync → log in
3. Pick a remote vault name (e.g., `secondbrain`)
4. Wait for the initial sync to finish
5. Repeat on each device — same login, pick the same remote vault

**Pros:**
- Encrypted end-to-end
- No file conflicts (Obsidian handles concurrent edits)
- Fast incremental sync
- Works on mobile (Obsidian iOS / Android)
- Zero filesystem futzing

**Cons:**
- Costs money
- Locks you into Obsidian's ecosystem (though your data is still plain Markdown — you can export anytime)

---

## Option 2: iCloud Drive (free, Mac-focused)

**Cost:** free (assuming you have iCloud storage)
**Best for:** Mac-only users who already use iCloud Drive

Setup:
1. Move your vault into iCloud Drive: typically `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/<vault-name>/`
2. In Obsidian, open the vault from the new location
3. Update your `OBSIDIAN_API_KEY` and any vault-path env vars to point to the new location
4. On other Macs: open Obsidian and pick the same iCloud Drive folder as the vault

**Pros:**
- Free (up to your iCloud storage limit)
- Built-in to macOS
- Works on iPhone/iPad with the Obsidian mobile app

**Cons:**
- Mac-only (no Windows/Linux iCloud support that's reliable)
- Known sync conflicts when editing the same file from two devices simultaneously
- iCloud sometimes "stalls" and files don't propagate for hours

**Gotchas:**
- Don't keep the vault BOTH in iCloud Drive AND somewhere else — pick one source of truth
- The `.obsidian/` config directory inside the vault sometimes confuses iCloud — if you see weird errors, exclude `.obsidian/` from sync if you can

---

## Option 3: Google Drive mount (free, what Stjepan uses)

**Cost:** free (up to 15GB)
**Best for:** users who already have Google Drive desktop installed

Setup:
1. Install Google Drive desktop: https://www.google.com/drive/download/
2. Sign in and let it create a `Google Drive` folder in your home directory
3. Move your vault into the Google Drive folder: `~/Google Drive/My Drive/secondbrain-vault/`
4. In Obsidian, open the vault from the new location
5. Update your env vars to point at the new path
6. On other devices: install Google Drive desktop, sign in with the same account, the vault folder will sync down

**Pros:**
- Free (15GB is plenty for years of vault usage)
- Cross-platform (Windows, Mac, Linux via the third-party `google-drive-ocamlfuse`)
- Reliable — Google has been running this for a decade

**Cons:**
- Initial sync of a large existing vault can be slow
- The Google Drive desktop app uses RAM
- File conflicts if you edit the same file from two devices at once (Google Drive will create `<file> (1).md`)

**Gotchas:**
- Make sure Google Drive is set to "Stream" or "Mirror" mode appropriately — Mirror keeps files local, Stream loads on-demand. For a vault you query constantly, Mirror is better
- Google Drive sometimes flags large `.obsidian/` plugin caches as suspicious — if you see warnings, click "trust"

---

## Option 4: Syncthing (free, peer-to-peer, advanced)

**Cost:** free (open source)
**Best for:** privacy-conscious users who don't want any third-party in the middle

Setup:
1. Install Syncthing on each device: https://syncthing.net/downloads/
2. On your primary device, add your vault as a "shared folder"
3. On each other device, install Syncthing and pair it with your primary device's ID
4. Add the same shared folder on each device, pointing to where you want the vault locally
5. Wait for initial sync

**Pros:**
- Truly peer-to-peer (no cloud, no third-party servers)
- End-to-end encrypted
- Free
- Cross-platform
- Works for very large vaults

**Cons:**
- Requires Syncthing running on all devices
- Devices must be online at the same time to sync (no "store and forward" via cloud)
- Setup is more complex than the cloud options
- No mobile support that's as polished as Obsidian Sync

**Gotchas:**
- If you have 3+ devices, you need to pair them all with each other (it's a mesh, not a hub)
- Syncthing's web UI is on `localhost:8384` — check it occasionally to make sure sync is healthy
- Network changes (new wifi, VPN) can confuse Syncthing's discovery — sometimes you have to manually re-add devices

---

## Option 5: Single-device only (skip sync)

**Cost:** free (no sync at all)
**Best for:** users who only ever use the plugin from one machine

This is fine. You don't need sync if you only use one laptop. The vault lives in `~/vault/` (or wherever you put it during `/secondbrain:init`), the agent reads/writes it directly, and you never touch it from anywhere else.

The downside: if your hard drive dies, you lose the vault. **Make backups.** Even Time Machine is enough.

If you later decide you want sync, run `/secondbrain:init` again — the sync method choice step is part of init.

---

## Switching sync methods later

You can switch sync methods anytime. The general pattern:
1. **Stop** the current sync method (turn off Obsidian Sync, unlink from Google Drive, etc.)
2. **Move** the vault folder to wherever the new method expects it
3. **Update** the `VAULT_PATH` env var (if you set one) and any references to the old path
4. **Start** the new sync method
5. **Re-open** the vault in Obsidian from the new location
6. Run `/secondbrain:doctor` to verify everything still works

The vault is just plain Markdown files — there's nothing magical about its location. The agent talks to it via the Obsidian Local REST API plugin, which doesn't care where on disk the vault lives.
