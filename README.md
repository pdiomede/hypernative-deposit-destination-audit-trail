# Deposit Destination Audit Trail

Hypernative Custom Agents that answer one question: **after we deposit,
where do the funds actually land and are held?**

Each agent watches one protocol, fires on a single supply/deposit made by a
monitored Safe, and emits a plain-English audit line, e.g.:

```
Deposit: 10.513985 USDC from Safe 0x37d3...38e7 -> Aave v3 USDC reserve
(Pool 0x8787...4E2, aToken 0x98c2...6f5c). Safe now holds 36.653238 aEthUSDC.
Tx 0xfb19...b7f2 @ block 25926411.
```

Built with the Hypernative Agents SDK (`invariantive`), under Onchain
Monitoring & Automated Response. Does not use Address Screener / Illicit
Funds Tracing.

## Setup

1. Requires the Hypernative Agents SDK (`invariantive`) and `web3`.
2. `cp config.env.example config.env` and fill in your Hypernative API
   credentials. `config.env` is git-ignored and auto-loaded by
   `shared/common.py`; nothing is read from it yet beyond that.
3. Notification channel id in `NOTIFICATION_CHANNEL_IDS` (`shared/common.py`).
4. `SAFE_LIST_UUID` (`shared/common.py`): create a Hypernative List of the
   Safes you want to monitor (Settings > Lists, columns `chain,address,note`).
   A wrong UUID fails silently — zero findings, no error.
5. Morpho vaults in scope: run `python3 discover_positions.py`, paste
   the result into `MORPHO_VAULTS_IN_SCOPE` in `shared/common.py`.
6. Chains: Ethereum and Base, for Aave v3 and Morpho Blue only (`CHAINS` in
   `shared/common.py`). Morpho Vaults stay Ethereum-only for now. To add a
   chain, add an entry to `CHAINS` with that chain's own Aave Pool / Morpho
   Blue addresses (they differ per chain — Morpho Blue happens to share one
   address across Ethereum and Base, but that's not to be assumed generally).
7. Check your plan's quota — pools/contracts, custom agents, chains, and
   automated actions all count against it. Each of the two agents below now
   deploys **one per chain**, so quota use doubles accordingly.

Then uncomment the `agent.deploy(...)` block in each agent file. Severity is
`Info`; new agents take up to 3 minutes to activate.

## Run

Three scripts, one per protocol. In plain terms:

- **`agent_aave_v3_supply.py`** — watches Aave v3. When a monitored Safe
  deposits, it tells you how much, into which asset, and what receipt
  token (aToken) the Safe now holds because of it.
- **`agent_morpho_blue_supply.py`** — watches Morpho Blue. Same idea, but
  for deposits made straight into one of Morpho's own lending markets.
- **`agent_morpho_vault_deposit.py`** — watches Morpho Vaults (curated
  pools like Steakhouse or Gauntlet). Catches deposits made *through* a
  vault instead of directly into a market — the Morpho Blue script above
  can't see these on its own, since the vault (not the Safe) is what
  Morpho Blue sees depositing.

```bash
python3 agent_aave_v3_supply.py
python3 agent_morpho_blue_supply.py
python3 agent_morpho_vault_deposit.py
```

Each replays real Ethereum mainnet fixtures (no Base fixtures yet, see
Verification) and prints the audit line plus every extracted variable,
grouped into plain-English sections (**Who**, **What was deposited**,
**Where it landed**, **Transaction**). Fixtures skip the Safe filter;
production keeps it and builds one agent per chain in `CHAINS`.

Add `--quiet` (or `-q`) for a demo-friendly version: only the audit lines,
wrapped, colourised, one blank line apart — the same text a real alert
would carry, safe to screen-share.

```bash
python3 agent_aave_v3_supply.py --quiet
```
```
Deposit: 10,000 USDT from Safe 0xd3bd6e2080bd49ab871b97d36cda9b07bdbdf396
  -> Aave v3 USDT reserve (Pool 0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2,
  aToken 0x23878914efe38d27c4d67ab83ed1b93a74d4086a). Safe now holds
  60,011.438176 aEthUSDT. Tx 0x24932d89...b60b89e77 @ block 25926404.
```

Colour and wrapping switch off when output isn't a terminal, so
`--quiet | grep` still gives one line per finding. `NO_COLOR=1` drops colour
but keeps the wrapping.

## Tools

**`discover_positions.py`** — "where is this Safe's money *right now*?"
Give it one or more wallet/Safe addresses and it checks, live, what each
one currently holds on Aave v3 and in known Morpho vaults. No transaction
hash needed, no Hypernative account needed — it just reads the chain
directly. No config needed beyond the address(es):

```bash
python3 discover_positions.py 0xSafe1 0xSafe2
```

It sweeps every chain in `CHAINS` (Ethereum + Base) for each Safe: Aave v3
is checked exhaustively (every current reserve, via `getReservesList()`);
Morpho vaults are checked on Ethereum only, against the fixed
`MORPHO_VAULT_UNIVERSE` list, so a vault not in that table won't show up.

```
##############################################################################
ETHEREUM -- connected at block 25932245
##############################################################################

==============================================================================
Safe 0xc540D6E077A3E70CC20B0E15AC50c8aFBE8fAe68
==============================================================================

  Morpho vaults:
    (no balances in the verified vault universe)

  Aave v3:
    total collateral ~ $5.00 | total debt ~ $0.00
    HOLDS  aEthUSDC                 5.000004   (reserve 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48)
```

If a chain's public RPC rate-limits mid-sweep, a reserve that couldn't be
checked is reported explicitly rather than silently counted as "not held":

```
    NOTE: 8/67 reserve(s) could not be checked (RPC error or rate limit) --
    re-run to confirm nothing was missed:
      0x9f8F72aA9304c8B593d555F12eF6589cC3A579A2
      ...
```

Just re-run the command when you see that note — it's usually a transient
rate limit (Base's public RPC especially), not a real problem. If it keeps
happening, point `rpc` in `CHAINS` (`shared/common.py`) at a dedicated
endpoint instead of the public default.

Found a Morpho vault position? Paste the `MORPHO_VAULTS_IN_SCOPE` block it
prints at the end into `shared/common.py`, then rebuild the vault agent.

## Structure

```
.
├── agent_aave_v3_supply.py        # run: Aave v3 Pool `Supply`
├── agent_morpho_blue_supply.py    # run: Morpho Blue market `Supply`
├── agent_morpho_vault_deposit.py  # run: Morpho Vault (V1/V1.1/V2) `Deposit`
├── discover_positions.py          # run: read-only sweep of Safe positions
├── shared/common.py               # config, verified addresses, fixtures
├── rules/rule_*_<chain>.json      # generated: exported rules for the API/Terraform
├── config.env.example             # copy to config.env, fill in, never commit config.env
└── LICENSE.md / CHANGELOG.md
```

Both Morpho files are needed: a vault deposit never appears in Morpho Blue's
`Supply` event (the vault does, as `onBehalf`), so Morpho Blue alone misses it.

## Verification

Every signature, arg index, selector, and return order was verified against
primary sources (protocol source, live chain reads, Sourcify, Etherscan),
then confirmed by running the agents against real transactions.

| Agent | Fixtures | Result |
|---|---|---|
| Aave v3 | 3 | Correct at 6dp/18dp; aToken matches independent source |
| Morpho Blue | 3 | Virtual-shares maths confirmed to the cent |
| Morpho Vault | 4 | Decimals kept separate; V2 works on the V1 event shape |

**Gaps:**
- No fixture has a Safe as the receiver — correct by construction, untested
  against a real one. Close it by replaying a real deposit from one of your
  monitored Safes.
- Base support (Aave v3 + Morpho Blue) is untested by fixture replay — no
  Base transaction has been run through either agent yet. The two Base
  addresses (Aave v3 Pool, Morpho Blue) were verified on Basescan (Exact
  Match) but not against a live Base deposit.
- Morpho Blue's contract address is identical on Ethereum and Base (CREATE2
  deploy), so the alert text itself cannot say which chain a Morpho Blue
  deposit happened on — only the agent name / rule file per chain can. The
  Aave v3 agent doesn't have this problem: its Pool address differs per chain.

## Notes worth keeping

- Filters use the receipt-token recipient (`onBehalfOf`/`onBehalf`/`owner`),
  never `tx_from` (a Safe multisig sets that to the owner's EOA).
- Aave's indexed args are declared out of order; `emitted_arg_N` follows
  declaration order, resolved empirically by replaying a real tx (see
  CHANGELOG.md 0.0.1) and recorded in `shared/common.py`.
- A vault's `decimals()` isn't its asset's decimals (`DECIMALS_OFFSET` = 12
  on stablecoin vaults); scaled separately.
- Morpho share conversion uses virtual shares, not a naive proportion.
- Hypernative's "Morpho Protocol Configuration Guide" doc page names the
  wrong contract and event shape; not used here.

## License

MIT — see [LICENSE.md](LICENSE.md).
