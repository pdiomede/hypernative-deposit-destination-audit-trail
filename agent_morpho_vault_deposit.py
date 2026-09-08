"""
Morpho Vault deposit destination (audit trail)

Fires on every ERC-4626 `Deposit` into an in-scope Morpho Vault on Ethereum
where the shares land on a monitored Safe, and emits one plain-English
audit line.

WHY THIS FILE EXISTS ALONGSIDE agent_morpho_blue_supply.py
    These are two genuinely different deposit routes and one does not see the
    other. If a Safe deposits into a curated vault (Steakhouse USDC, Gauntlet
    USDC Prime, ...), the VAULT is what later supplies Morpho Blue, so the
    Morpho Blue `Supply` event carries the vault as `onBehalf` and the Safe
    never appears in it. A Morpho Blue agent alone would be permanently silent
    for a vault depositor. Both files are needed to cover "Morpho".

ONE EVENT COVERS ALL THREE VAULT GENERATIONS
    event Deposit(address indexed sender, address indexed owner,
                  uint256 assets, uint256 shares)
    MetaMorpho V1 inherits it from OpenZeppelin IERC4626. MetaMorphoV1_1 is
    identical. Morpho Vault V2 re-declares it in its own EventsLib with the
    second parameter renamed `owner` -> `onBehalf`, but identical types,
    indexed flags and order, hence the same topic0
    (0xdcbc1c05240f31ff3ad067ef1ee35ce4997762752e3a095284754544f4c709d7).
    Verified empirically: V2 logs decode with the V1 shape. So this one agent
    shape serves V1, V1.1 and V2 with no branching.

WHY THE FILTER IS ON owner
    ERC4626._deposit does `_mint(receiver, shares)` where receiver is the
    `owner` topic, so `owner` holds the position and `sender` merely paid.
    This is not a corner case: a scan of ~79k blocks found 20 deposits where
    sender != owner, almost all with sender = Morpho's Bundler3 adapter
    0x4a6c312ec70e8747a587ee860a0353cd42be0ae0, which is the route the Morpho
    UI uses. If a deposit goes through the Morpho UI, `sender` will be that
    adapter and the Safe will be `owner`. Filtering on sender or tx_from would
    miss those deposits entirely.

!! THE DECIMALS TRAP -- THE MOST LIKELY BUG IN THIS FILE !!
    A vault's decimals() is NOT its asset's decimals. OpenZeppelin returns
    underlyingDecimals + DECIMALS_OFFSET, and DECIMALS_OFFSET is 12 on every
    stablecoin Morpho vault. So in ONE steakUSDC log, `assets` is 6-decimal
    and `shares` is 18-decimal. Formatting both the same way is wrong by 10^12.
    Verified: steakUSDC.convertToAssets(1e18) = 1139169, i.e. 1e18 shares is
    about 1.139169 USDC.
    RULE, applied below: format `assets` and any convertToAssets output with
    the ASSET's decimals; format `shares` with the VAULT's own decimals.
    steakETH is the DECIMALS_OFFSET = 0 control case in the fixtures.

ABI RESOLUTION
    Morpho vaults are direct non-proxy CREATE deployments with verified source
    at the vault address, so simple names resolve. Etherscan shows a
    FALSE-POSITIVE "may be a proxy" banner on them (OpenZeppelin Multicall
    self-delegatecall); Blockscout correctly reports proxy_type: null. Read the
    ABI at the vault address and ignore the proxy hint.
"""

from invariantive.model import (Agent, AlertConfig, ContextVariable, ContractScoreVariable,
                                EventTrigger, GenericContractReadVariable,
                                PythonProcessingVariable, RunConfig, TokenBalanceVariable)
from invariantive.model.trigger import Condition

# NOTE: NOTIFICATION_CHANNEL_IDS and SEVERITY read as unused to a linter.
# They are referenced by the commented-out agent.deploy(...) block at the
# bottom of this file. Do not remove them.
from shared.common import (CHAIN, SAFE_LIST_UUID, MORPHO_VAULTS_IN_SCOPE,
                           NOTIFICATION_CHANNEL_IDS, SEVERITY, VAULT_ARG_ASSETS,
                           VAULT_ARG_OWNER, VAULT_ARG_SENDER, VAULT_ARG_SHARES,
                           VAULT_EVENT, VAULT_TX_SENDER_NE_OWNER, VAULT_TX_USDT_SIMPLE,
                           VAULT_TX_V2, VAULT_TX_WETH_OFFSET0, get_cli_flag,
                           get_cli_tx_hashes, is_quiet_mode, print_findings)


def build_audit_line(extracted_variables):
    """Compose the audit line for one Morpho Vault deposit.

    SANDBOX RULES OBSERVED: constants inside the function (no module globals),
    imports inside the function, no `+=`, no tuple unpacking, no inline
    ternary, no leading-underscore names, f-strings only.
    """
    try:
        safes_list_uuid = "SAFE_LIST_UUID"
        bundler_adapter = "0x4a6c312ec70e8747a587ee860a0353cd42be0ae0"

        vault = extracted_variables.get("vault_address")
        vault_symbol = extracted_variables.get("vault_symbol")
        vault_name = extracted_variables.get("vault_name")
        vault_decimals = extracted_variables.get("vault_decimals")
        safe_address = extracted_variables.get("safe_address")
        sender = extracted_variables.get("sender")
        assets_raw = extracted_variables.get("assets_raw")
        asset_symbol = extracted_variables.get("asset_symbol")
        asset_decimals = extracted_variables.get("asset_decimals")
        shares_after_raw = extracted_variables.get("shares_after_raw")
        assets_after_raw = extracted_variables.get("assets_after_raw")
        tx_hash = extracted_variables.get("tx_hash")
        block_number = extracted_variables.get("block_number")

        # Two DIFFERENT decimal scales. Keeping them separate is the whole
        # point: conflating them is wrong by 10^12 on any stablecoin vault.
        if asset_decimals is None:
            asset_decimals = 18
        if vault_decimals is None:
            vault_decimals = 18
        if asset_symbol is None:
            asset_symbol = "token"
        if vault_symbol is None:
            vault_symbol = "vault shares"
        if vault_name is None:
            vault_name = "Morpho Vault"

        asset_scale = float(10 ** int(asset_decimals))
        vault_scale = float(10 ** int(vault_decimals))

        # `assets` -> the ASSET's decimals.
        amount = float(int(assets_raw)) / asset_scale
        amount_text = f"{amount:,.6f}".rstrip("0").rstrip(".")
        # A deposit smaller than 0.0000005 rounds to "0" at 6dp. Reporting
        # "Deposit: 0 USDC" for a real transfer of value is a misstatement in an
        # audit record, so fall back to exact base units instead.
        if amount_text == "0" and int(assets_raw) > 0:
            amount_text = f"{int(assets_raw)} base units of"

        # Share balance -> the VAULT's decimals.
        if shares_after_raw is None:
            shares_text = "unavailable"
        else:
            shares_value = float(int(shares_after_raw)) / vault_scale
            shares_text = f"{shares_value:,.6f}".rstrip("0").rstrip(".")
            # Same rounding trap on the share balance.
            if shares_text == "0" and int(shares_after_raw) > 0:
                shares_text = f"{int(shares_after_raw)} base units of"

        # convertToAssets output -> the ASSET's decimals.
        value_clause = ""
        if assets_after_raw is not None:
            assets_after = float(int(assets_after_raw)) / asset_scale
            assets_after_text = f"{assets_after:,.6f}".rstrip("0").rstrip(".")
            # Same rounding trap on the redeemable value.
            if assets_after_text == "0" and int(assets_after_raw) > 0:
                assets_after_text = f"{int(assets_after_raw)} base units of"
            value_clause = f", currently redeemable for about {assets_after_text} {asset_symbol}"

        safe_label = ""
        try:
            note = list_client.get_note(safes_list_uuid, safe_address, chain.ethereum)
            if note:
                safe_label = f" ({note})"
        except Exception:
            safe_label = ""

        # Material audit fact, and common here because of the Morpho UI route.
        initiator_note = ""
        mismatch = False
        if sender and safe_address:
            if str(sender).lower() != str(safe_address).lower():
                mismatch = True
                if str(sender).lower() == bundler_adapter:
                    initiator_note = (
                        f" NOTE: submitted via the Morpho bundler ({sender}), "
                        f"not directly by the Safe."
                    )
                else:
                    initiator_note = f" NOTE: initiated by {sender}, not the holding Safe."

        description = (
            f"Deposit: {amount_text} {asset_symbol} from Safe {safe_address}{safe_label} "
            f"-> {vault_name} vault ({vault} on Morpho). "
            f"Safe now holds {shares_text} {vault_symbol}{value_clause}. "
            f"Tx {tx_hash} @ block {block_number}.{initiator_note}"
        )

        return {
            "audit_line": description,
            "amount": amount,
            "amount_text": amount_text,
            "shares_text": shares_text,
            "initiator_mismatch": mismatch,
        }
    except Exception as exception:
        return {
            "audit_line": f"Morpho Vault deposit detected but formatting failed: {exception}",
            "amount": 0,
            "amount_text": "?",
            "shares_text": "?",
            "initiator_mismatch": False,
        }


def extract_audit_line(extracted_variables):
    """Surface the composed line as a flat variable for the alert template."""
    formatted = extracted_variables.get("formatted")
    if formatted is None:
        return "Morpho Vault deposit detected (formatter produced no output)"
    return formatted.get("audit_line")


def build_agent(vault_address, apply_safe_filter=True):
    """Assemble one agent for one vault.

    An EventTrigger takes a single concrete contract_address, so there is one
    agent per vault. That is why this is a factory rather than a module-level
    agent. Each vault also counts against your plan's pools/contracts limit.

    apply_safe_filter=False builds the test shape used by the fixture replay in
    __main__. NEVER deploy the unfiltered shape.
    """
    agent = Agent(
        trigger=EventTrigger(
            chain=CHAIN,
            contract_address=vault_address,
            event_sig=VAULT_EVENT,
            output_index="emitting_contract",
            operator="compare_exact",
            operands=[vault_address],
        )
    )

    # Safe filter on `owner`, applied before the contract reads.
    # !! A WRONG LIST UUID FAILS SILENTLY (0 findings, no error) !!
    if apply_safe_filter:
        agent.add_trigger_condition(
            Condition(
                output_index=VAULT_ARG_OWNER,
                operator="in_lists",
                operands=[SAFE_LIST_UUID],
            )
        )

    # Event args and transaction identity.
    agent.add_variable(ContextVariable(output_index=VAULT_ARG_SENDER, var_name="sender"))
    agent.add_variable(ContextVariable(output_index=VAULT_ARG_OWNER, var_name="safe_address"))
    agent.add_variable(ContextVariable(output_index=VAULT_ARG_ASSETS, var_name="assets_raw"))
    agent.add_variable(ContextVariable(output_index=VAULT_ARG_SHARES, var_name="shares_minted"))
    agent.add_variable(ContextVariable(output_index="emitting_contract", var_name="vault_address"))
    agent.add_variable(ContextVariable(output_index="tx_hash", var_name="tx_hash"))
    agent.add_variable(ContextVariable(output_index="block_number", var_name="block_number"))
    agent.add_variable(ContextVariable(output_index="block_timestamp", var_name="block_timestamp"))
    agent.add_variable(ContextVariable(output_index="tx_from", var_name="executed_by"))

    # ----------------------------------------------------------------------
    # Vault identity. The vault address is static per agent, so the simple
    # func_sig form resolves (these are verified non-proxy deployments).
    # ----------------------------------------------------------------------
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address=vault_address, func_sig="name",
            input=[], output_index="output_arg_0", var_name="vault_name",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address=vault_address, func_sig="symbol",
            input=[], output_index="output_arg_0", var_name="vault_symbol",
        )
    )
    # The SHARE decimals. Deliberately a separate variable from asset_decimals:
    # they differ by DECIMALS_OFFSET (12 on stablecoin vaults).
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address=vault_address, func_sig="decimals",
            input=[], output_index="output_arg_0", var_name="vault_decimals",
        )
    )
    # The underlying asset.
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address=vault_address, func_sig="asset",
            input=[], output_index="output_arg_0", var_name="underlying",
        )
    )
    # Asset metadata. Dynamic address, so full signature + ABI types required.
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address="extracted_variables.underlying",
            func_sig="decimals()", input=[], input_data_type=[],
            output_data_type=["uint8"], output_index="output_arg_0",
            var_name="asset_decimals",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address="extracted_variables.underlying",
            func_sig="symbol()", input=[], input_data_type=[],
            output_data_type=["string"], output_index="output_arg_0",
            var_name="asset_symbol",
        )
    )

    # ----------------------------------------------------------------------
    # The position after the deposit. The vault IS the ERC-20 share token, so
    # balanceOf on the vault address is the Safe's share balance.
    #
    # Two reads: the scaled one is for humans, the raw one is what
    # convertToAssets needs as input.
    # ----------------------------------------------------------------------
    # Raw (unscaled) only. convertToAssets needs raw base units as input, and
    # the formatter scales for display using the VAULT's decimals, so a second
    # pre-scaled read would be a wasted on-chain call on every matching event.
    agent.add_variable(
        TokenBalanceVariable(
            chain=CHAIN, token_address=vault_address,
            token_holder_address="extracted_variables.safe_address",
            scale_to_decimals=False, var_name="shares_after_raw",
        )
    )
    # What those shares are worth in the underlying asset right now. Output is
    # in the ASSET's decimals, not the vault's.
    agent.add_variable(
        GenericContractReadVariable(
            chain=CHAIN, contract_address=vault_address, func_sig="convertToAssets",
            input=["extracted_variables.shares_after_raw"],
            output_index="output_arg_0", var_name="assets_after_raw",
        )
    )

    # Risk scores -- LOCAL DEMO ONLY, gated on the test shape so the deployed
    # agent and exported rule JSON stay free of them. See the equivalent block
    # in agent_aave_v3_supply.py for the full reasoning.
    if not apply_safe_filter:
        agent.add_variable(
            ContractScoreVariable(
                chain=CHAIN, contract_address="extracted_variables.underlying",
                var_name="underlying_risk_score",
            )
        )
        agent.add_variable(
            ContractScoreVariable(
                chain=CHAIN, contract_address=vault_address,
                var_name="vault_address_risk_score",
            )
        )

    agent.add_variable(
        PythonProcessingVariable(source_code=build_audit_line, var_name="formatted")
    )
    agent.add_variable(
        PythonProcessingVariable(source_code=extract_audit_line, var_name="audit_line")
    )

    agent.set_alert(
        AlertConfig(
            var_name="audit_line", operator="any", operands=[],
            description="{{audit_line}}",
            involved_assets={
                "safe_address": "Monitored Safe",
                "vault_address": "Morpho Vault (receipt token)",
                "underlying": "Asset deposited",
            },
        )
    )
    return agent


# --------------------------------------------------------------------------
# One agent per in-scope vault. MORPHO_VAULTS_IN_SCOPE is intentionally empty
# until you confirm which vaults you use -- run discover_positions.py to
# find out. Building agents for vaults you do not use would burn your
# pools/contracts and custom-agent quota for nothing.
# --------------------------------------------------------------------------
agents = {}
for vault in MORPHO_VAULTS_IN_SCOPE:
    vault_agent = build_agent(vault["address"], apply_safe_filter=True)
    vault_agent.save_config(f"rules/rule_morpho_vault_{vault['symbol']}.json")
    agents[vault["symbol"]] = vault_agent

# Suppressed under --quiet/-q: this notice is operational noise, not part of
# the audit output a customer should see on screen.
if not MORPHO_VAULTS_IN_SCOPE and not is_quiet_mode():
    print(
        "NOTE: MORPHO_VAULTS_IN_SCOPE in shared/common.py is empty, so no vault agents "
        "were built.\n      Run discover_positions.py, confirm your vaults, "
        "then populate it."
    )


# ==========================================================================
# DEPLOY -- intentionally commented out. One deploy() per vault. See README.md
# for the pre-deploy checklist (channel id, list UUID, chains, quota).
# Channel id: Actions > Notification Channels > open channel > id in the URL,
# or GET https://api.hypernative.xyz/notification-channels
# (headers: x-client-id, x-client-secret).
# ==========================================================================
# for symbol in agents:
#     agent_id = agents[symbol].deploy(
#         agent_name=f"Morpho Vault {symbol} deposit destination (audit trail)",
#         severity=SEVERITY,                                  # "Info"
#         channels_configurations=NOTIFICATION_CHANNEL_IDS,   # e.g. [{"id": 1337}]
#     )
#     print(f"deployed {symbol} agent id: {agent_id}")


if __name__ == "__main__":
    # Real historical Ethereum mainnet Morpho Vault deposits -- either the
    # four built-in verification fixtures, or a specific tx hash passed on
    # the command line together with the vault that emitted it (there's no
    # single fixed vault address here, unlike the Aave/Morpho Blue agents,
    # since this agent takes a vault address as a build-time parameter):
    #   python3 agent_morpho_vault_deposit.py 0xTxHash --vault=0xVaultAddress [--quiet]
    #
    # Unfiltered shape, since none of these owners is necessarily a
    # monitored Safe.
    #
    # --quiet / -q : print only the ALERT lines, no debug variable dump.
    # Demo-friendly for screen-sharing with a customer.
    quiet = is_quiet_mode()
    custom_hashes = get_cli_tx_hashes()
    custom_vault = get_cli_flag("vault")

    if custom_hashes:
        if not custom_vault:
            print("Pass --vault=0xVaultAddress together with the tx hash, e.g.:")
            print("  python3 agent_morpho_vault_deposit.py 0xTxHash --vault=0xVaultAddress")
            raise SystemExit(1)
        fixtures = [(custom_vault, tx_hash, "custom tx") for tx_hash in custom_hashes]
    else:
        fixtures = [
            ("0xbEef047a543E45807105E51A8BBEFCc5950fcfBa", VAULT_TX_USDT_SIMPLE,
             "steakUSDT V1: 480,234.499741 USDT, 6dp asset / 18dp shares"),
            ("0xBEEf050ecd6a16c4e7bfFbB52Ebba7846C4b8cD4", VAULT_TX_WETH_OFFSET0,
             "steakETH V1: 0.049909 WETH, DECIMALS_OFFSET=0 control"),
            ("0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB", VAULT_TX_SENDER_NE_OWNER,
             "steakUSDC V1: 200 USDC via Morpho bundler, sender != owner"),
            ("0x04422053aDDbc9bB2759b248B574e3FCA76Bc145", VAULT_TX_V2,
             "kUSDC VAULT V2: 2,042,581.5 USDC, same event shape as V1"),
        ]
    for vault_address, tx_hash, label in fixtures:
        test_agent = build_agent(vault_address, apply_safe_filter=False)
        result = test_agent.run(RunConfig(chain=CHAIN, hashes=[tx_hash]))
        print_findings(result, f"Morpho Vault: {label}", quiet=quiet)
