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
5. Morpho vaults in scope: run `python3 tools/discover_positions.py`, paste
   the result into `MORPHO_VAULTS_IN_SCOPE` in `shared/common.py`.
6. Scoped to Ethereum mainnet only; extend `CHAIN` in `shared/common.py` for
   another chain (Aave/Morpho addresses differ per chain, so update those too).
7. Check your plan's quota — pools/contracts, custom agents, chains, and
   automated actions all count against it.

Then uncomment the `agent.deploy(...)` block in each agent file. Severity is
`Info`; new agents take up to 3 minutes to activate.

## Run

```bash
python3 agent_aave_v3_supply.py
python3 agent_morpho_blue_supply.py
python3 agent_morpho_vault_deposit.py
```

Each replays real mainnet fixtures and prints the audit line plus every
extracted variable, grouped into plain-English sections (**Who**, **What was
deposited**, **Where it landed**, **Transaction**). Fixtures skip the Safe
filter; production keeps it.

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

## Structure

```
.
├── agent_aave_v3_supply.py         # run: Aave v3 Pool `Supply`
├── agent_morpho_blue_supply.py     # run: Morpho Blue market `Supply`
├── agent_morpho_vault_deposit.py   # run: Morpho Vault (V1/V1.1/V2) `Deposit`
├── shared/common.py                # config, verified addresses, fixtures
├── tools/
│   ├── discover_positions.py       # run: read-only sweep of Safe positions
│   └── probe_aave_args.py          # resolves Aave's emitted_arg_N mapping
├── rules/rule_*.json               # generated: exported rules for the API/Terraform
├── config.env.example              # copy to config.env, fill in, never commit config.env
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

**Gap:** no fixture has a Safe as the receiver — correct by construction,
untested against a real one. Close it by replaying a real deposit from one
of your monitored Safes.

## Notes worth keeping

- Filters use the receipt-token recipient (`onBehalfOf`/`onBehalf`/`owner`),
  never `tx_from` (a Safe multisig sets that to the owner's EOA).
- Aave's indexed args are declared out of order; `emitted_arg_N` follows
  declaration order, resolved via `tools/probe_aave_args.py`.
- A vault's `decimals()` isn't its asset's decimals (`DECIMALS_OFFSET` = 12
  on stablecoin vaults); scaled separately.
- Morpho share conversion uses virtual shares, not a naive proportion.
- Hypernative's "Morpho Protocol Configuration Guide" doc page names the
  wrong contract and event shape; not used here.

## License

MIT — see [LICENSE.md](LICENSE.md).
