# Changelog

All notable changes to these deposit-destination agents.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Pending your own configuration before a deployable release:

- `SAFE_LIST_UUID`, notification channel id, the Safe addresses to monitor.
- `MORPHO_VAULTS_IN_SCOPE`, from `discover_positions.py`.
- Replay against a real monitored-Safe deposit; no fixture has one yet.
- Replay a real Base deposit through the Aave v3 / Morpho Blue agents; no
  Base fixture exists yet (Base addresses verified on Basescan only).

## [0.0.12] - 2026-09-08

Split the audit line into two paragraphs: what moved, then what the Safe
holds now. This changes the real alert text, not just the local view.

### Changed

- `build_audit_line()` in all three agents now puts a blank line before
  "Safe now holds". Deployed agents send the same two paragraphs, so the
  local view stays byte-for-byte identical to the alert (the property this
  repo has deliberately kept). `rules/*.json` regenerated accordingly.
- `format_audit_line()` wraps each paragraph separately. A single
  `textwrap.wrap()` call over the whole string would have silently eaten the
  break -- `replace_whitespace=True` turns `\n` into a space.
- Piped output folds the break back to a space, so the documented
  `--quiet | grep` recipe still yields one line per finding.

## [0.0.11] - 2026-09-08

Added a Hypernative risk score for every contract involved in a deposit
(asset, receipt token, pool/market), shown in the grouped CLI output. This
is a **local demo feature only**: the scores are added to the unfiltered
test shape, so deployed agents and the exported `rules/*.json` are byte-for-
byte unchanged and pay no extra per-event cost.

### Added

- `ContractScoreVariable` (from the same `invariantive` SDK we already use)
  added per contract in all three agents, gated on `apply_safe_filter=False`:
  `reserve`/`atoken`/`pool_address` (Aave v3),
  `loan_token`/`collateral_token`/`market_contract` (Morpho Blue),
  `underlying`/`vault_address` (Morpho Vault).
- `print_variables()` in `shared/common.py` renders the SDK's `-1` ("not
  scored yet") sentinel as `not available (...)` instead of a bare `-1`,
  which would otherwise read as a literal (and confusingly negative) score.

### Notes

- Considered and rejected for this pass: Hypernative's literal "Pool
  Toxicity API" (screens an AMM liquidity pool's LP composition for
  sanctions/mixer exposure) — it's built for Uniswap-style pools
  specifically and needs pre-created Policy IDs plus real
  `x-client-id`/`x-client-secret` credentials, neither of which fits Aave
  reserves or Morpho markets. `ContractScoreVariable` needed zero extra
  setup, verified live.
- Return shape observed empirically (undocumented scale): a float, small
  values near 0 for reputable contracts (USDT 0.00047, USDC 0.00000004),
  `-1` when a contract hasn't been scored (observed on the Aave v3 Pool
  proxy itself).

## [0.0.10] - 2026-09-08

Added command-line tx-hash replay, for pulling up a specific real deposit
(e.g. a customer's own) on demand instead of only the built-in fixtures.

### Added

- `python3 agent_*.py 0xTxHash [0xTxHash2 ...]` replays those transactions
  instead of the built-in fixtures. Works alongside `--quiet`.
- `--chain=base` on the Aave v3 / Morpho Blue agents (default `ethereum`).
- `--vault=0xVaultAddress` on the Morpho Vault agent, required together with
  a tx hash since that agent builds one instance per vault rather than per
  chain; errors with usage guidance if the hash is given without it.
- `get_cli_tx_hashes()` / `get_cli_flag()` in `shared/common.py`, shared by
  all three `__main__` blocks the same way `is_quiet_mode()` already was.

## [0.0.9] - 2026-09-08

Removed `tools/probe_aave_args.py` (its one job was already done and
recorded; see 0.0.1) and moved `discover_positions.py` to the repo root.
`tools/` no longer exists.

### Removed

- `tools/probe_aave_args.py`. The `emitted_arg_N` mapping it derived is
  already hardcoded in `shared/common.py`; nothing imports this file, and
  re-deriving that mapping is only ever needed if Aave changes the `Supply`
  event, which is now undocumented as a re-runnable script rather than
  removed as a capability -- see CHANGELOG history (0.0.1) for the record
  of how it was derived.

### Changed

- `discover_positions.py` moved from `tools/` to the repo root -- it's a
  script you run directly, same as the three `agent_*.py` files, not a
  dependency of anything else. Invocation is now `python3
  discover_positions.py ...` (no `tools/` prefix); the `sys.path` bootstrap
  it needed inside `tools/` is gone too.

## [0.0.8] - 2026-09-08

Fixed `tools/discover_positions.py` appearing to hang, and a real
misreporting bug it was masking. Found live: a Safe with a genuine $5 Aave
v3 position on Base printed "no aToken balances" -- the reserve that
actually held it had silently failed with a rate-limit error, caught by a
blanket `except: continue` that treated "couldn't check" identically to
"not held".

### Fixed

- `check_aave()` now collects and reports which reserves errored (RPC
  failure or 429 rate limit) instead of silently skipping them, so a
  nonzero collateral total with zero HOLDS lines is no longer a silent
  contradiction -- it prints exactly which reserves need a re-run.
- Added a 15s request timeout to each chain's `Web3.HTTPProvider`. Without
  one, a rate-limited public RPC (`mainnet.base.org` especially) doesn't
  error, it just goes quiet for a very long time under web3.py's default
  retry-with-backoff behavior -- easy to mistake for a true hang.

### Notes

- Public RPCs, especially Base's, rate-limit under the burst of per-reserve
  calls this script makes (2-4 calls per Aave reserve). If reserves keep
  erroring on re-run, point `rpc` in `CHAINS` (`shared/common.py`) at a
  dedicated endpoint instead of the public default.

## [0.0.7] - 2026-09-08

Added Base support for Aave v3 and Morpho Blue (Morpho Vaults stay
Ethereum-only). `discover_positions.py` now sweeps every configured chain.

### Added

- `CHAINS` in `shared/common.py`: per-chain Aave v3 Pool / Morpho Blue /
  RPC config, Ethereum and Base. `build_agent(chain_key=...)` in both agents
  now builds and exports one agent + rule file per chain
  (`rules/rule_*_<chain>.json`).
- Base addresses (Aave v3 Pool, Morpho Blue) verified on Basescan (Exact
  Match) -- not yet confirmed by replaying a real Base transaction.

### Fixed

- The Aave/Morpho Blue alert formatters hardcoded the Pool/Morpho Blue
  address as a literal, which would have printed the Ethereum address on a
  Base alert too. Now read from the triggering event's own
  `emitting_contract` instead.

### Known limitation

- Morpho Blue deploys to the identical address on Ethereum and Base
  (CREATE2), so that agent's alert text can't say which chain fired by
  address alone -- disambiguated by agent name / rule file per chain instead.

## [0.0.6] - 2026-09-08

Reorganized the repo layout and added `config.env` for credentials.

### Changed

- Root now holds only the three `agent_*.py` entry points. `common.py`
  moved to `shared/`; `discover_positions.py`/`probe_aave_args.py` moved to
  `tools/`; generated `rule_*.json` moved to `rules/`.
- Added `config.env` / `config.env.example` (git-ignored / tracked
  template) for Hypernative API credentials, auto-loaded by
  `shared/common.py`.

## [0.0.5] - 2026-09-08

Genericized the tool for any deployment, and grouped the default terminal
output into plain-English sections. No change to what any agent detects.

### Changed

- Removed all customer-specific naming (`HECATE_SAFES_LIST_UUID` ->
  `SAFE_LIST_UUID`, `HECATE_SAFES` -> `SAFES_TO_CHECK`, plus every
  customer-specific docstring/comment) and added `LICENSE.md` (MIT).
- Default (non-`--quiet`) output now groups each finding into Who / What
  was deposited / Where it landed / Transaction, with `initiator_mismatch`
  shown as a sentence instead of a buried bool inside a duplicated dict.

## [0.0.4] - 2026-09-07

Made `--quiet` readable on a projector. Display only, no agent logic touched.

### Added

- `--quiet` output is now wrapped to the terminal width (capped at 100
  columns), colourised, and separated by a blank line between findings.
  Amounts green, addresses dim, `NOTE:` clause yellow, `Deposit:` bold cyan.
- Raw ANSI, no new dependency. `rich` and `colorama` are both absent, and a pip
  dependency for cosmetics is a bad trade for a script that may run on a
  colleague's or the customer's laptop.
- `is_terminal()`, `supports_color()`, `highlight()` and `format_audit_line()`
  in `common.py`. Wrapping follows `is_terminal()`; colour additionally honours
  `NO_COLOR`. The two are deliberately independent, so setting `NO_COLOR` as a
  standing preference does not put the unwrapped wall of text back.
- Redirected output is unchanged: one line per finding, no escape codes, so the
  documented `grep` recipes keep working.

### Fixed

Two bugs caught while building this, both by the fidelity check that compares
rendered output against the raw `description`:

- **Colour codes were corrupting each other.** Applying the hex and number
  patterns as sequential `re.sub()` calls re-scanned already-coloured text, and
  the number pattern then matched the digits *inside* an ANSI escape (the `2`
  and `37` of `\033[2;37m`), injecting colour into the middle of a colour code.
  Now a single pass over one combined pattern, which never revisits its own
  output.
- **A wrapped `NOTE:` clause lost its highlight** after the first line, because
  the pattern was line-anchored. The clause is now treated as one block across
  however many lines it wraps to.

### Notes

- Colour lives in `print_findings()` only. `build_audit_line()` in the agents
  is untouched: ANSI codes in that string would corrupt the real Slack and
  email alert.
- Verified the rendered text still round-trips identically to the raw alert
  after stripping escape codes and rejoining wrapped lines.
- Wrapping uses `break_long_words=False` and `break_on_hyphens=False`, so a
  42-char address, a 66-char hash, and names like `PT-reUSD-10DEC2026` are
  never split.

## [0.0.3] - 2026-09-07

Added a demo-friendly output mode. No functional changes to any agent's logic.

### Added

- `--quiet` / `-q` flag on all three agent scripts' `__main__` block. Prints
  only the ALERT line(s), nothing else, no banner, no finding count, no
  extracted-variable dump. Meant for screen-sharing an agent replay directly
  with a customer.
- `is_quiet_mode()` in `common.py`, shared by all three scripts so the flag
  behaves identically everywhere.
- `print_findings(result, title, quiet=...)` in `common.py` now takes the
  quiet flag; default behaviour (no flag) is unchanged.

### Fixed

- The vault agent's "MORPHO_VAULTS_IN_SCOPE is empty" notice printed even
  under `--quiet`, since it runs at import time rather than through
  `print_findings`. Now suppressed in quiet mode too.

## [0.0.2] - 2026-09-07

Bug-fix pass over 0.0.1. Five bugs found and fixed. All 10 fixtures still
produce identical output, no regressions.

### Fixed

- **Dust amounts printed as zero.** Values under 0.0000005 rendered as `0`
  at 6dp, so a real deposit could read "Deposit: 0 WETH". Now falls back to
  exact base units when rounding would hit zero.
- **Wasted on-chain call per event** in the vault agent: `shares_after` was
  read and never used. Removed.
- **`discover_positions.py` could silently miss a position.** It scaled
  `convertToAssets` with a hard-coded decimals table; a hand-added vault entry
  missing that field raised `KeyError`, swallowed by the surrounding `except`.
  Now reads asset decimals on-chain instead.
- **Test blocks hardcoded `Chain.ethereum`** instead of `CHAIN`, contradicting
  the "chain is a config edit" design.
- **Dead locals removed** (`reserve`, `loan_token`), extracted and unused.

### Changed

- Annotated `NOTIFICATION_CHANNEL_IDS`/`SEVERITY` imports, which read as
  unused to a linter but are used by the commented-out `deploy()` block.

## [0.0.1] - 2026-09-07

First working version. Three agents, validated against real mainnet
transactions.

### Added

- `agent_aave_v3_supply.py`: fires on Aave v3 `Supply`, resolves the aToken
  via `getReserveAToken`, reports the Safe's post-deposit balance.
- `agent_morpho_blue_supply.py`: fires on Morpho Blue `Supply`, resolves the
  market id to its tokens and LLTV, converts shares to assets via Morpho's
  virtual-shares formula.
- `agent_morpho_vault_deposit.py`: fires on ERC-4626 `Deposit`. One agent per
  vault. One event signature covers MetaMorpho V1, V1.1, and Vault V2.
- `common.py`: shared config, verified addresses, arg mappings, fixtures.
- `discover_positions.py`: read-only sweep of vault/Aave positions per Safe.
  No Hypernative credentials needed, no quota consumed.
- `probe_aave_args.py`: resolves Aave's `emitted_arg_N` mapping empirically.
- `README.md` with the pre-deploy checklist and verification status.

### Verified

- Aave v3 `Supply` argument indices, by probe against a tx where `user` and
  `onBehalfOf` differ. Hypernative numbers by declaration order.
- Simple `event_sig="Supply"` resolves through Aave's proxy.
- `getReserveAToken` and `getReserveData()[8]` return the same aToken.
- `TokenBalanceVariable` reads state at the triggering block, not chain head.
- Morpho Blue `Supply`/`idToMarketParams`/`position`/`market` signatures and
  return order, against source and live mainnet reads.
- Morpho Vault `Deposit` topic0 is identical across V1, V1.1, and V2.

### Notes

- Severity set via `deploy()`, not `AlertConfig` (which has no severity field).
- All agents filter on the receipt-token recipient, never `tx_from` (a Safe
  multisig sets that to the owner's EOA).
- A wrong List UUID yields zero findings and no error.
- Hypernative's "Morpho Protocol Configuration Guide" doc page is stale and
  was not used.
