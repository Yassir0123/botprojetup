# Stabilization And Refactor Plan

This plan is written to protect the existing game logic first. The card stat handling, equipment, team support boosts, charms, enhancement, and fight calculations are tightly connected, so the first rule is: do not rewrite those systems directly until their current behavior is captured.

## Main Goals

1. Stop transient Discord/network errors from breaking missions, chapters, and user state.
2. Make multi-user/session handling safer without changing game balance.
3. Make database transactions real and consistent.
4. Preserve card/team/equipment/stat behavior exactly unless a bug is proven.
5. Gradually reduce complexity after the bot is stable.

## What The Shown Errors Mean

The repeated errors are mostly not card-stat bugs. They are Discord HTTP/network failures during message edits:

- `Cannot connect to host discord.com:443 ssl:default [getaddrinfo failed]`
- `[WinError 64] Le nom réseau spécifié n’est plus disponible`
- `503 Service Unavailable`
- `upstream connect error or disconnect/reset before headers`
- `asyncio.exceptions.CancelledError`

They happen around calls like:

```py
await interaction.message.edit(embed=embed)
await battle_message.edit(embed=embed)
```

The dangerous part is not the Discord error itself. The dangerous part is what happens after it:

- A mission can already have modified in-memory health/state.
- A user can remain stuck in `mission_current`, `gamble_status`, `trade_status`, `market_session`, etc.
- A reward or health update may be partially applied if DB and Discord edits are mixed in the same flow.
- Error reporting itself may fail if Discord is down, creating noisy repeated failures.

So the plan focuses first on making Discord calls resilient and making state cleanup reliable.

## Non-Negotiable Safety Rules

1. Do not change stat formulas until we have behavior snapshots.
2. Do not rename columns or tables during stabilization.
3. Do not merge `team.py`, `inventory.py`, `spawn_see.py`, `dungeons.py`, or `fight_challenge_embed.py` into new architecture immediately.
4. Do not change card power, attack, health, defence, speed, charms, equipment, support boosts, or enhancement math casually.
5. Every risky change must be behind a small helper with the same external behavior.
6. Database mutations that belong together must use one connection and one transaction.
7. Discord message edit failures must not leave users stuck.

## Phase 0: Baseline And Protection

Purpose: understand and freeze current behavior before changing internals.

Tasks:

1. Create a lightweight behavior map for the most sensitive flows:
   - `sl ar` / `arise`
   - `sl team`, `sl add`, `sl remove`, `sl swap`
   - `sl equip`, `sl unequip`
   - `sl charm`, `sl hcharm`
   - `sl enhance`, `sl henhance`
   - `sl mission`
   - `sl dungeon`
   - `sl fight`
   - `sl ascend`

2. Add snapshot-style test fixtures later, using copied representative rows from the database structure, not production secrets.

3. Capture expected calculations for:
   - base card stats
   - stat points
   - equipment boosts
   - support boosts
   - charm boosts
   - skill effects
   - current health handling
   - power recalculation

4. Document which tables each command mutates.

No production logic changes in this phase.

## Phase 1: Discord HTTP Resilience

Purpose: fix the class of errors shown in the screenshots without touching battle/stat formulas.

### 1. Add A Safe Discord Operation Helper

Create a helper module later, for example:

```py
utils/discord_safe.py
```

It should wrap:

- `message.edit`
- `ctx.send`
- `interaction.response.send_message`
- `interaction.followup.send`
- `user.send`

Behavior:

- retry only transient errors
- use short exponential backoff
- do not retry permission errors
- do not retry invalid message/channel errors
- return `True/False` or a result object instead of raising for known transient failures

Transient errors to handle:

- `discord.errors.DiscordServerError`
- `aiohttp.ClientConnectorError`
- `aiohttp.ClientOSError`
- `aiohttp.ServerDisconnectedError`
- `aiohttp.ClientResponseError` for 5xx
- `ConnectionResetError`
- `asyncio.TimeoutError`

Do not swallow:

- `discord.Forbidden`
- `discord.NotFound`
- `discord.HTTPException` with non-transient 4xx
- code bugs like `TypeError`, `KeyError`, bad SQL, etc.

### 2. Apply First To Mission And Adventure Only

First targets:

- `pages/missions.py`
- `pages/adventure.py`

Reason: the pasted errors are from mission/chapter message edits.

Replace high-risk calls like:

```py
await interaction.message.edit(embed=embed)
```

with a safe wrapper:

```py
ok = await safe_edit_message(interaction.message, embed=embed)
```

If the edit fails after retries:

- continue cleanup
- do not duplicate rewards
- avoid sending another Discord error message if Discord is unavailable
- log compactly

### 3. Separate Gameplay Result From Discord Rendering

For mission/adventure combat:

1. Calculate next battle state.
2. Persist only what must be persisted.
3. Try to render/edit Discord message.
4. If rendering fails, cleanup status and return gracefully.

Discord rendering should never be the only path to cleanup.

## Phase 2: State Cleanup And User-Stuck Prevention

Purpose: make sure users do not get locked forever.

### 1. Centralize Session/Status Cleanup

Current state tables include:

- `trade_status`
- `gamble_status`
- `market_session`
- `sell_status`
- `shop_status`
- `ascending_state`
- `stat_status`
- `quiz_status`

Create one helper later:

```py
utils/session_state.py
```

Responsibilities:

- check whether a user is busy
- acquire a session
- release a session
- force cleanup stale sessions
- report which operation is blocking the user

### 2. Add Expiry Fields Where Possible

Ideal table pattern:

```sql
user_id
operation_type
created_at
expires_at
metadata
```

Conservative approach:

- do not delete existing status tables immediately
- add `created_at` / `expires_at` to current tables if missing
- cleanup expired rows before rejecting a user

### 3. Add Database-Level Uniqueness

For each status table, enforce only one active row per user:

```sql
UNIQUE KEY unique_user_status (user_id)
```

Then use atomic insert:

```sql
INSERT INTO gamble_status (user_id, created_at, expires_at)
VALUES (...)
```

If duplicate key happens, the user is already busy.

This avoids the race:

```py
SELECT no row
SELECT no row from another command
INSERT both
```

### 4. Cleanup Must Be In `finally`

Any flow that acquires state must release it in `finally`.

Important targets:

- mission start/combat
- chapter start/combat
- fight/challenge
- trade
- market listing creation
- sell
- shop
- ascend
- stat allocation
- quiz/tutorial

## Phase 3: Database Connection And Transaction Fixes

Purpose: make DB operations reliable under multiple users.

### 1. Use Only One Database Manager

Current files:

- `utils/database.py`
- `utils/seconddatabase.py`

Plan:

1. Find imports of `seconddatabase.py`.
2. If unused, leave it ignored/deprecated first.
3. Later remove or archive it after confirming nothing imports it.

Do not keep two live pools unless there is a specific reason.

### 2. Simplify Pool Behavior Carefully

Current `utils/database.py` has:

- pool
- warm connections
- keep alive
- semaphore
- query queue
- metrics

The pool is useful. The custom warm-connection and queue behavior should be reviewed carefully.

Conservative path:

1. Keep the existing pool initially.
2. Add better metrics around pool size, free connections, query failures.
3. Do not rewrite the pool during the first bug-fix pass.
4. Later remove unused queue/warm-connection complexity if it is not helping.

### 3. Fix Real Transaction Bugs

High priority:

- `pages/vote.py` has a transaction helper using `START TRANSACTION`, then separate calls to `execute_query_with_retry`, then `COMMIT`.

That is unsafe because each call may use a different pooled connection.

Plan:

1. Replace that helper with `utils.database.execute_transaction`.
2. Ensure every transaction uses one acquired connection.
3. Audit other files for manual `START TRANSACTION`, `COMMIT`, `ROLLBACK`.

### 4. Protect Read-Modify-Write Updates

Any logic like:

```py
SELECT quantiter
UPDATE quantiter = old + x
```

should become:

```sql
UPDATE inventory_product
SET quantiter = quantiter + %s
WHERE inventory_id = %s AND product_id = %s
```

or run inside a transaction with row locks.

This matters for:

- rewards
- inventory item counts
- currency
- market purchases
- trades
- daily/vote rewards
- dungeon rewards

## Phase 4: Mission And Adventure Stability

Purpose: fix the exact failing systems first.

### Mission

Targets in `pages/missions.py`:

- `start_mission`
- `process_mission_victory`
- `reset_mission_state`
- all `interaction.message.edit` calls
- error reporting to support

Plan:

1. Add safe Discord edit/send wrapper.
2. Make battle loop tolerant of one failed edit.
3. Ensure mission state resets even if final message edit fails.
4. Do not grant rewards twice if retry/edit fails after DB reward was applied.
5. Make error reporting non-blocking and failure-safe.
6. Ensure `current_health` never stays wrong because Discord failed.

### Adventure / Chapter

Targets in `pages/adventure.py`:

- `chapter_start`
- battle message edits
- reward handling
- battle cleanup

Plan:

1. Use the same safe edit wrapper.
2. Keep combat math unchanged.
3. Ensure chapter state cleanup runs even if Discord edit fails.
4. Ensure rewards are transactional and idempotent where possible.

## Phase 5: Card/Team/Equip Stat Safety Net

Purpose: protect the tight card stat logic before touching it.

This phase is about tests and diagnostics, not refactoring formulas.

### 1. Identify The Canonical Stat Formula

Current stat influences appear to include:

- base card stats
- level/current level
- stat points
- attack/defence/speed/health counts
- equipment
- halloween equipment
- charms
- support cards
- skills
- ascension/caps
- enhancement
- current team position
- offence/support role

Need to document where each is applied:

- `team.py`
- `spawn_see.py`
- `inventory.py`
- `fight_challenge_embed.py`
- `dungeons.py`
- `missions.py`
- `adventure.py`
- `ascend.py`

### 2. Build A Stat Snapshot Tool

Create a read-only diagnostic command or script later:

```py
tools/stat_snapshot.py
```

Input:

- user id
- card id

Output:

- base stats
- current `user_cards` stats
- equipment boosts
- support boosts
- charm boosts
- final calculated stats
- power stat

This tool must not update the database.

### 3. Add Regression Fixtures

Use known examples:

- one naked card
- one equipped card
- one card with charms
- one full team with support boost
- one enhanced card
- one ascended card
- one card in active fight/mission

Expected result:

- current behavior preserved exactly.

Only after this should stat formulas be moved or cleaned.

## Phase 6: Market, Trade, Inventory Safety

Purpose: prevent duplication, loss, and race bugs.

### Trade

Plan:

1. Ensure trade creation is atomic.
2. Ensure both users cannot be in another operation.
3. Ensure final trade processing uses one DB transaction.
4. Deduct and add all items/currency in one transaction.
5. Add unique constraints or lock rows for active trade status.
6. Keep UI behavior unchanged.

### Market

Plan:

1. Ensure listing creation reserves/removes items atomically.
2. Ensure buying validates listing still active at purchase time.
3. Ensure two users cannot buy the same listing.
4. Ensure failed purchase rolls back cleanly.
5. Ensure `market_session` always clears.

### Inventory

Plan:

1. Do not refactor all of `inventory.py` at once.
2. Start with shared helpers for quantity add/remove.
3. Make item count updates atomic.
4. Add checks so quantity never goes below 0.
5. Preserve embed/category UI.

## Phase 7: Vote System Fixes

Purpose: make Top.gg voting safe and consistent.

Plan:

1. Replace manual transaction helper with real `execute_transaction`.
2. Do not trust default hardcoded Top.gg token; use environment variable.
3. Keep webhook reward logic idempotent:
   - one reward per 12-hour window
   - duplicate webhook should not double-reward
4. Ensure inventory reroll insert/update is atomic.
5. Keep reminder task but prevent noisy failures if Discord DM fails.

## Phase 8: Logging And Error Reporting

Purpose: make logs useful without huge files or repeated spam.

Plan:

1. Keep `.log` files ignored.
2. Replace `print` gradually with structured logging.
3. Add rotating log handlers.
4. Compress repeated Discord network errors into summary logs.
5. Do not dump full user/card data into public channels unless needed.
6. Redact tokens/secrets.

For support-channel error messages:

- include command, user, guild, channel
- include compact error type
- include operation id
- avoid giant repeated traceback spam when Discord is already failing

## Phase 9: Secrets And Configuration

Purpose: avoid accidental leaks and make deployment safer.

Plan:

1. Move Discord token out of `config.py`.
2. Move Top.gg token/secret out of code.
3. Use `.env` or environment variables.
4. Keep `config.py` as a loader only.
5. Rotate exposed tokens.

No gameplay logic changes here.

## Phase 10: Controlled Refactor After Stabilization

Only after phases 1-5 are stable:

Split code by responsibility:

- `services/stats.py`
- `services/inventory.py`
- `services/sessions.py`
- `services/rewards.py`
- `services/discord_safe.py`
- `services/quests.py`

But this must be gradual. For example, start by moving one pure helper at a time:

1. copy function
2. test against old behavior
3. replace call sites
4. remove old duplicate only after confidence

## Suggested Work Order

### Pass 1: Low-Risk Stabilization

1. Add safe Discord edit/send helpers.
2. Apply only to `missions.py`.
3. Apply only to `adventure.py`.
4. Fix mission/adventure cleanup paths.
5. Fix `vote.py` transaction helper.

### Pass 2: State Safety

1. Create session-state helper.
2. Add cleanup for expired session rows.
3. Convert one system at a time:
   - mission
   - market
   - trade
   - ascend
   - shop/sell

### Pass 3: Data Safety

1. Audit inventory quantity updates.
2. Convert reward updates to atomic SQL.
3. Convert market/trade finalization to stricter transactions.

### Pass 4: Stat Protection

1. Create read-only stat snapshot tool.
2. Capture expected outputs.
3. Only then consider stat refactors.

## What I Would Not Touch First

Do not start by rewriting:

- `inventory.py`
- `spawn_see.py`
- `team.py`
- card/equipment/charm/support formulas
- fight formulas
- dungeon reward balance
- SQL schema broadly

Those are too central. Stabilize the edges first: Discord calls, cleanup, transactions, and locks.

## Acceptance Checklist

Before considering the bot stabilized:

1. Mission Discord edit failures no longer leave users stuck.
2. Adventure chapter edit failures no longer leave users stuck.
3. Vote rewards use a real transaction.
4. Status/session tables cannot create duplicate active sessions for one user.
5. Expired sessions can self-clean.
6. Network errors are retried briefly and logged compactly.
7. Card/team/equip stat outputs match pre-change snapshots.
8. Market/trade item transfers cannot partially apply.
9. No source file needs to read `.log` files.
10. Secrets are not committed in code.

## Current Stabilization Checkpoint

Completed in the current layer:

1. Dungeon rewards: key consume, timer update, victory rewards, quest progress, chest/product/orb/currency drops, and rare weapon drops are handled before reward display.
2. Adventure chapter rewards: chapter completion, first-clear chest grants, charm count, XP, level, affinity, and stat rewards are grouped before reward display.
3. Fight rewards: fight stat update, quest progress, and XP/affinity rewards now run as one fight-result DB operation; reward embeds are sent only after that DB work commits.
4. Idempotency guard: mission, dungeon, chapter, and fight reward commits now consume their existing active-state flag inside the reward transaction, so a replay/resume sees the flag already released and does not grant rewards again.
5. Layer 5A inventory/stat audit started: `inventory.py` sensitive stat commands were mapped, equip/unequip/rstat/level/xpboost/hxpboost/capsule/hcapsule are guarded by the inventory advisory lock and transaction wrapper, and reset item consumption now re-locks the inventory product row inside its transaction before consuming/applying the reset.
6. Layer 5B current-health safety: `current_health` is confirmed as runtime battle HP in loaded fight/mission/dungeon/adventure paths, while `current_healthpoints` is stat allocation in inventory/spawn flows. Runtime health-stat buffs in fight and dungeon now clamp current HP to the new max without healing or reviving.
7. Layer 5C equip/unequip deep pass: normal and Halloween equip flows now lock the consumed/returned equipment inventory row during the existing transaction, normal equip/unequip refuses Halloween gear so it cannot be converted into normal inventory, and stat/support/equipment formulas were left unchanged.
8. Layer 5D capsule/rstat pass: `rstat`, `capsule`, and `hcapsule` now re-lock the selected card row and inventory product row before consuming items and recalculating stats. The existing statpoint/base-stat/equipment/support formula branches were preserved.
9. Layer 5E level/XP boost pass: `level`, `xpboost`, and `hxpboost` now lock the selected card row plus the consumed fragment/product row before XP, level, stat, and inventory mutations. Max-level savings, enhancer/equipment/support branches, and stat gain formulas were preserved.
10. Layer 5F enhancer/charm safety: `enhance`, `henhance`, `charm`, and `hcharm` now re-lock the selected card row and consumed product row inside the existing short consume/apply transaction. Charm/enhancer application now targets `id_ligneusercards` with active-state guards instead of updating by card name, while product IDs, charm counts, fight counts, and formulas were preserved.
11. Layer 5G snapshot/diagnostic tool: `tools/card_snapshot.py` was added as a read-only JSON snapshot helper for one owned card. It reports stored base/current stats, power checks, equipment IDs/details, support/team state, statpoint/used-point fields, level/XP, charms, and enhancer state without mutating database rows.
12. Layer 5H final inventory mutation audit: remaining inventory quantity paths were swept. Chest opening now re-locks the chest row before consume, skip dungeon pass consumes the pass and updates user dungeon state in one locked transaction, exchange re-locks source/target capsule rows inside its transaction, skin equip/unequip re-locks skin/card rows before mutation, fragment sale rechecks locked fragments in the final sale transaction, and pickaxe equip now uses a short locked final swap after Discord confirmation to return the old pickaxe, consume the new pickaxe, update `user.pickaxe_id`, and clear `pickaxe_status` atomically.
13. Layer 6 verification pass: loaded extension targets were checked against `main.py`, the high-risk inventory/stat/reward paths were re-mapped, mission/dungeon/adventure/fight reward commits were rechecked for DB-first reward handling, and `tools/compare_card_snapshot.py` was added as a read-only before/after checker for `tools/card_snapshot.py` JSON output. The comparer reports tracked stat/equipment/charm/level changes and warns on impossible states such as power mismatches, current health above max health, level above max level, or snapshots from different card/user rows.
14. Layer 7 runtime cleanup pass: `utils.activity_state.cleanup_stale_user_activity` was added to release only proven-stale activity flags using existing timer columns. Mission, dungeon, fight, and the shared activity blocker now attempt stale cleanup before rejecting a user as already active. The cleanup only clears `fight_current`, `mission_current`, or `dungeon_current` when the matching timer is at least 30 minutes old. `chapter_count` is intentionally not auto-expired because the current schema has no chapter timer column, and market/sell/shop/gamble marker rows are not auto-cleaned because they do not expose a safe age marker in the inspected code. Trade already has `created_at` timeout cleanup.
15. Layer 8 Pass 1 load/infra safety: hot loaded paths were audited for pressure points, including mission/dungeon/adventure/fight battle loops, inventory mutations, market listing views, trade timeout cleanup, and DB pool usage. `utils.database` now holds the connection semaphore for the full checked-out connection lifetime instead of releasing it immediately after acquisition, so the intended concurrent connection cap actually applies under load. Query worker startup now tops up to the target worker count instead of duplicating workers after pool reinitialization, cancelled query workers exit cleanly, and warm idle connections no longer inflate active-connection metrics. Command lock cleanup in the loaded mission/fight/database registries now prunes only unlocked entries, so a large registry cleanup cannot drop an actively running command lock. Trade timeout cleanup was confirmed to already use `created_at`; marker-only market/sell/shop/gamble status rows still need schema-free caution because they do not expose a safe age marker in code.
16. Layer 8 Pass 2 Discord/backpressure safety: `utils.discord_safe.safe_edit` now supports opt-in `min_interval` throttling, with bounded per-message state, so hot battle loops can coalesce progress edits instead of hammering Discord while final/result/attachment edits remain unthrottled. Mission, adventure chapter, dungeon, and fight progress-only embed edits now use a 1-second minimum interval. Dungeon start and chapter start now use per-user advisory locks before their deeper setup paths, reducing duplicate-start pressure before the DB activity flag is claimed. No combat math, rewards, item consumption, or final result logic was changed.
17. Layer 8 Pass 3 stress verification report: all 27 extensions loaded by `main.py` compile, and the core stabilized files compile as a set. Load safety now has three layers: DB backpressure at the pool/connection level, per-user command/activity/status guards before high-risk flows, and Discord edit throttling in hot battle progress loops. Recommended live-safe verification is read-only metrics first (`get_metrics`/existing DB stats commands), then test-server command bursts with test accounts only. Remaining high-load caveats are intentional: market/sell/shop/gamble marker rows are not auto-expired without a safe timestamp, chapter auto-expiry is still avoided because there is no chapter timer column, and full market/trade purchase atomicity deserves its own dedicated layer before calling the economy layer complete.
18. Layer 9 error reporting cleanup: `utils.error_reporting` was added for compact support reports with shared context, exception type/message, traceback tail, and compact card/opponent summaries. Mission, dungeon, adventure chapter, and fight support reports now use one compact helper instead of sending multiple large Discord messages or full battle payloads. Dead full-payload report builders were removed from those hot paths. User-facing error behavior stays simple and gameplay/reward/stat logic was not changed.
19. Layer 10 final hygiene/verification pass: touched stabilization helpers, diagnostic tools, and high-risk loaded pages were rechecked for obvious stale imports and whitespace issues. No risky cleanup was needed in the gameplay files. The touched-file compile check passed, all 27 extensions loaded by `main.py` compile, and `git diff --check` is clean for the stabilized target files except for Git's existing line-ending notice on `utils/database.py`. Remaining worktree noise includes unrelated log/pycache/status changes; those were intentionally not touched in this pass.
20. Economy/chest hardening pass: market listing creation now locks the seller inventory row, verifies listed quantities, creates the market row, creates listing rows, and decrements seller inventory inside one transaction using the connection-local inserted market id. Market delist now locks the listing, listing rows, and seller inventory, returns all items, and deletes the listing/market rows in one transaction. Market purchase and trade final exchange were rechecked and already use short transaction/row-lock paths from earlier layers. Chest `loot` now prepares the same random/fixed reward plan first, then consumes the chest, grants item/currency rewards, and updates the "Open 5 chests" quest in one transaction before sending the Discord embed. Reward amounts, random chances, taxes, item ids, and prices were kept unchanged.

Layer 8 stress-safe test checklist:

1. Use a test account/server, not live economy accounts, for mutation tests.
2. Start 5-10 simultaneous mission/dungeon/fight/chapter attempts from the same user and confirm only one starts.
3. Start simultaneous activities from different users and confirm unrelated users are not globally blocked.
4. Watch DB metrics during bursts: active connections should stay under the semaphore cap, query workers should not multiply, and waiting query count should not grow permanently.
5. During long battles, confirm progress still updates but Discord edit failures do not crash/reward twice.
6. Use `tools/card_snapshot.py` and `tools/compare_card_snapshot.py` around any stat/equip/item mutation tested on a test card.

Layer 6 read-only usage:

```powershell
python tools\card_snapshot.py --discord-id 209663598572732416 --card-name "Igris" > before.json
python tools\card_snapshot.py --discord-id 209663598572732416 --card-name "Igris" > after.json
python tools\compare_card_snapshot.py before.json after.json
```

Only run the actual bot command between the two snapshots on a test account/card, because commands like equip, capsule, chest open, or boost consume live items.

Preserved:

- No schema changes.
- No production data cleanup or destructive database action.
- No reward amounts, drop chances, combat formulas, PvP/challenge result rules, or card stat formulas intentionally changed.

## Final Strategy

The safe way to fix this project is not a big rewrite. The safe way is to put guardrails around the existing logic, especially around Discord HTTP failures and database/session consistency. Once those guardrails exist, the tight card stat systems can be protected with snapshots before any cleanup happens.

The first real code change should be small: add a safe Discord message helper and use it in `pages/missions.py` where the pasted errors happen. That gives immediate stability without touching card math.
