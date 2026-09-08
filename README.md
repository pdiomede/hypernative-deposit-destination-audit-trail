# Deposit Destination Audit Trail

Answers one question: **after we deposit, where do the funds actually land —
and are they still there?**

Covers **Aave v3** and **Morpho** (Blue markets + Vaults), on **Ethereum** and
**Base**.

| You want to know | Run this |
|---|---|
| "Where did *this deposit* go?" | `python3 agent_aave_v3_supply.py 0xYourTxHash` |
| "Where is my money *right now*?" | `python3 discover_positions.py 0xYourSafeAddress` |

---

## Try it now

### 1. Follow one deposit

Pass any real deposit transaction hash — yours or anyone's:

```bash
python3 agent_aave_v3_supply.py 0x035ce8b125e7133f07e7ec653ce037ed051dfecec8042d36412c92baf1de74a6 --quiet
```

```
Deposit: 5 USDC from Safe 0xc540d6e077a3e70cc20b0e15ac50c8afbe8fae68
  -> Aave v3 USDC reserve (Pool 0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2,
  aToken 0x98c23e9d8f34fefb1b7bd6a91b7ff122f4e16f5c).

Safe now holds 4.999998 aEthUSDC.
  Tx 0x035ce8b1...f1de74a6 @ block 25932163.
```

That's the whole point, in two parts: **what moved** (who deposited, how
much, into which protocol and pool), then **what the Safe holds now** as a
result. It's the exact text a real alert carries.

Same idea for the other two protocols:

```bash
python3 agent_morpho_blue_supply.py 0xYourTxHash --quiet
python3 agent_morpho_vault_deposit.py 0xYourTxHash --vault=0xVaultAddress --quiet
```

**You don't need to know which chain the transaction is on** — the script
checks Ethereum and Base and runs on whichever one has it, telling you which
it picked. Pass `--chain=base` to force one.

Other options: `--vault=` is required for the vault script · drop `--quiet`
for the full breakdown · pass several hashes at once to replay a batch.

**Run it with no arguments** and it replays built-in real mainnet
transactions instead — a quick way to see all the output shapes without
needing a tx hash of your own.

### 2. See where a wallet's money sits right now

No transaction hash needed — give it a Safe or wallet address and it reads
the chain live:

```bash
python3 discover_positions.py 0xc540D6E077A3E70CC20B0E15AC50c8aFBE8fAe68
```

```
##############################################################################
BASE -- connected at block 51046369  via custom RPC (base-mainnet.g.alchemy.com)
##############################################################################

==============================================================================
Safe 0xc540D6E077A3E70CC20B0E15AC50c8aFBE8fAe68
==============================================================================

  Morpho vaults (checking 8 known vaults):
    (none of the 8 known vaults held -- vaults outside this list are not checked)

  Aave v3:
    total collateral ~ $5.00 | total debt ~ $0.00
    HOLDS  aBasUSDC                 4.999999   (reserve 0x8335...2913)
      Pool toxicity -- Default PT Policy   [Aave V3]
        Policy Recommendation:      Deny
        Policy Aggregated Toxicity: 0.13%
        Flags triggered (4 of 15):
          Sanctions (OF1010)                                     < 0.01%  Medium
          Related to Sanctions (RF1010)                            0.02%  Medium
          Sanctioned Jurisdiction Exchange (OF1711)              < 0.01%  Medium
          Related to Sanctioned Jurisdiction Exchange (RF1711)   < 0.01%  Medium
```

It sweeps **Ethereum and Base**, checking every current Aave v3 reserve plus
the known Morpho vaults. Pass several addresses to check them all at once.

⚠️ **Aave and Morpho coverage differ.** Aave reserves are enumerated live
from the Pool, so *every* reserve is checked. Morpho vaults are matched
against a fixed list of 8 (`MORPHO_VAULT_UNIVERSE` in `shared/common.py`),
Ethereum only — so "none held" means none *of those 8*, and a vault outside
the list is invisible to the tool. Add its address there to include it.

**Pool toxicity** (the indented block above) is optional and only runs if you
put Hypernative API credentials in `config.env` — it screens *who else* is in
each pool you're exposed to, reporting how much of that liquidity traces back
to sanctioned, mixer-linked or hack-proceeds funds. The layout mirrors the
Flags Inspection panel in the Hypernative web app, so the numbers line up
with what you'd see there. Add `--no-toxicity` to skip it.

Read the **recommendation**, not the headline percentage — they routinely
disagree. Above, 0.13% looks harmless, but the verdict is `Deny` because
individual sanctions flags each blew past their own far lower thresholds.
Each flag carries its own threshold, so flag percentages aren't comparable
to each other either: a 0.055% flag can be clean while a 0.006% one is
Medium. Only flags that actually triggered are listed; the rest are counted.

> **Seeing `NOTE: 25/67 reserve(s) could not be checked (RPC error or rate
> limit)`?** That's the free public RPC throttling, not a problem with your
> wallet — a full sweep makes ~200 sequential calls. Failed reads are
> retried automatically, and it's reported explicitly rather than silently
> counted as "nothing held". To get rid of it, put your own endpoint in
> `config.env`:
>
> ```
> ETHEREUM_RPC_URL=https://your-provider.example/v2/YOUR_KEY
> BASE_RPC_URL=https://your-base-endpoint.example/v2/YOUR_KEY
> ```
>
> Any provider works (Alchemy, Infura, QuickNode, self-hosted) — paste the
> URL they give you. Those URLs contain your API key, so the tool only ever
> prints the host name, never the full URL.

---

## What you need

- Python 3, plus `web3` and the Hypernative Agents SDK (`invariantive`).
- `discover_positions.py` needs **nothing else** for the position sweep — no
  Hypernative account, no API key. It reads public RPCs directly.
- The three `agent_*.py` scripts need the Hypernative SDK installed and
  authenticated, since they run the real agent logic.

Optional: `cp config.env.example config.env` and add your Hypernative API
credentials (git-ignored). That switches on the pool toxicity screening in
`discover_positions.py`; without it, that step is skipped and everything else
works exactly the same. You also need at least one Pool Toxicity policy in
your account (Screener > Policies) — the API requires one and has no default.

---

## What each script does

| Script | Watches | Catches |
|---|---|---|
| `agent_aave_v3_supply.py` | Aave v3 | Deposits into any Aave v3 reserve; reports the aToken the Safe receives |
| `agent_morpho_blue_supply.py` | Morpho Blue | Deposits straight into a Morpho lending market |
| `agent_morpho_vault_deposit.py` | Morpho Vaults | Deposits *through* a curated vault (Steakhouse, Gauntlet, …) |
| `discover_positions.py` | — | Live snapshot: what a wallet holds right now |

Both Morpho scripts are needed. A vault deposit never shows up in Morpho
Blue's own `Supply` event — the *vault* appears there, not your Safe — so
the Morpho Blue script alone would miss it entirely.

## Output modes

Without `--quiet`, each deposit is broken into plain-English sections —
**Who**, **What was deposited**, **Where it landed**, **Transaction** — with
every underlying value shown. Each contract involved also gets a Hypernative
risk score (lower is safer; `not available` means it hasn't been scored).
These scores are a **local demo feature only** — deployed agents don't
compute them and don't pay for the extra calls.

With `--quiet`, you get only the audit lines: wrapped, colourised, one blank
line apart. This is the exact text a real alert carries, so it's safe to
screen-share. Colour and wrapping turn off automatically when piped, so
`--quiet | grep` still gives one line per finding — the alert's own
paragraph break is folded back to a space in that case, so each finding
stays greppable on one line (`NO_COLOR=1` keeps the wrapping but drops the
colour).

---

## Deploying it for continuous monitoring

Everything above runs on demand. To have it alert you automatically on every
new deposit:

1. **Notification channel** — put its id in `NOTIFICATION_CHANNEL_IDS`
   (`shared/common.py`). Find it under Actions > Notification Channels.
2. **Which Safes to watch** — create a Hypernative List (Settings > Lists,
   columns `chain,address,note`) and put its UUID in `SAFE_LIST_UUID`.
   ⚠️ A wrong UUID fails *silently*: zero findings, no error.
3. **Which Morpho vaults** — run `discover_positions.py`, paste the block it
   prints into `MORPHO_VAULTS_IN_SCOPE`.
4. **Check your quota** — the Aave v3 and Morpho Blue agents each deploy
   **one agent per chain**, so Ethereum + Base doubles the count.
5. Uncomment the `agent.deploy(...)` block in each agent file and run it.
   Severity is `Info`; new agents take up to 3 minutes to go live.

To add another chain, add an entry to `CHAINS` (`shared/common.py`) with that
chain's own Aave Pool and Morpho Blue addresses — they differ per chain.

---

## Reference

### Layout

```
.
├── agent_aave_v3_supply.py        # run: Aave v3 deposits
├── agent_morpho_blue_supply.py    # run: Morpho Blue deposits
├── agent_morpho_vault_deposit.py  # run: Morpho Vault deposits
├── discover_positions.py          # run: live position snapshot
├── shared/common.py               # config, verified addresses, fixtures
├── rules/rule_*_<chain>.json      # generated: rule exports for API/Terraform
└── config.env.example             # copy to config.env (never commit config.env)
```

### Verification status

Every signature, arg index, selector, and return order was verified against
primary sources (protocol source, live chain reads, Sourcify, Etherscan),
then confirmed by replaying real transactions.

| Agent | Fixtures | Result |
|---|---|---|
| Aave v3 | 3 | Correct at 6dp/18dp; aToken matches independent source |
| Morpho Blue | 3 | Virtual-shares maths confirmed to the cent |
| Morpho Vault | 4 | Decimals kept separate; V2 works on the V1 event shape |

**Known gaps:**
- No fixture has a monitored Safe as the receiver — correct by construction,
  but untested against a real one. Replay one of your own deposits to close it.
- Base is proven for **Aave v3** — a real Base deposit
  (`0x38eee4c5...dcdc162af`) was replayed end to end, with the agent resolving
  the Base Pool, Base USDC and `aBasUSDC` correctly. **Morpho Blue on Base**
  is still address-verified only (Basescan Exact Match), never replayed.
- Morpho Blue has the *same* contract address on Ethereum and Base (CREATE2),
  so its alert text can't tell you which chain fired — the agent name and rule
  file per chain do. Aave v3 isn't affected; its Pool address differs per chain.

### Implementation notes

- Filters use the receipt-token recipient (`onBehalfOf`/`onBehalf`/`owner`),
  never `tx_from` — a Safe multisig sets that to the executing owner's EOA.
- Aave declares its indexed args out of order; `emitted_arg_N` follows
  declaration order (resolved empirically, recorded in `shared/common.py`).
- A vault's `decimals()` is not its asset's decimals (`DECIMALS_OFFSET` = 12
  on stablecoin vaults) — the two are scaled separately.
- Morpho share conversion uses virtual shares, not a naive proportion.
- Risk scores are added only to the unfiltered test shape
  (`apply_safe_filter=False`), so they run on local CLI replays but never
  reach a deployed agent or the exported rule JSON.
- Hypernative's "Morpho Protocol Configuration Guide" doc page names the wrong
  contract and event shape; it was not used here.

## License

MIT — see [LICENSE.md](LICENSE.md).
