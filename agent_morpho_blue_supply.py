"""
Morpho Blue deposit destination (audit trail)

Fires on every Morpho Blue `Supply` on a supported chain (Ethereum or Base,
see CHAINS in shared/common.py) where the supply shares land on a monitored
Safe, and emits one plain-English audit line.

BASE SUPPORT AND THE SAME-ADDRESS QUIRK
    Morpho deploys Morpho Blue deterministically (CREATE2), so its address is
    IDENTICAL on Ethereum and Base -- confirmed independently on Etherscan and
    Basescan (both "Exact Match"). That means, unlike the Aave v3 agent, the
    contract address in this alert's own text can NOT tell a reader which
    chain a given deposit happened on. This is handled at the agent/rule
    level instead: one agent per chain, named "... (ethereum)"/"... (base)"
    and exported to separate rule_morpho_blue_supply_<chain>.json files, so
    the Hypernative dashboard disambiguates even though the alert text can't.
    Not yet confirmed by replaying a real Base transaction -- no Base fixture
    exists below, only Ethereum ones.

WHAT THE ALERT CONTAINS
    protocol (Morpho Blue) · the destination MARKET ID (the audit primary key)
    and the Morpho Blue contract · the loan token and decimal-adjusted amount ·
    the Safe's supply-share balance after the deposit, plus its value converted
    back to the underlying asset · the initiating Safe · tx hash and block

THE MARKET ID IS THE POINT
    Morpho Blue is not a single pool. It is thousands of isolated markets, each
    identified by a bytes32 id = keccak256(abi.encode(MarketParams)). "We
    deposited into Morpho" is not an auditable statement; "we deposited into
    market 0x1e9d6146...a4fc64, which lends USDC against PT-reUSD collateral at
    91.5% LLTV" is. The id is in the alert verbatim and resolves at
    app.morpho.org/market?id=<id>.

WHY THE FILTER IS ON onBehalf
    `onBehalf` receives the supply shares, so it holds the position. Filtering
    on `tx_from` would be wrong (a Safe execution has tx_from = the executing
    owner EOA) and filtering on `caller` would miss router-routed deposits. See
    the fixture MORPHO_TX_CALLER_NE_ONBEHALF, where a router is the caller.

SHARES -> ASSETS USES VIRTUAL SHARES. DO NOT SIMPLIFY THIS.
    Morpho's SharesMathLib adds virtual shares/assets to mitigate the ERC4626
    inflation attack:
        VIRTUAL_SHARES = 1e6, VIRTUAL_ASSETS = 1
        assets = shares * (totalSupplyAssets + 1) / (totalSupplyShares + 1e6)
    A naive `shares * total / totalShares` is WRONG. Also, totalSupplyAssets
    excludes interest accrued since the market's `lastUpdate`, so the converted
    figure is "as of last accrual" and the alert says exactly that rather than
    overstating precision.

VERIFIED FACTS THIS FILE DEPENDS ON
    * Morpho Blue 0xBBBB...FFCb is verified on Etherscan ("Exact Match",
      contract `Morpho`, full ABI, NOT a proxy), so simple function/event names
      resolve.
    * event Supply(Id indexed id, address indexed caller,
                   address indexed onBehalf, uint256 assets, uint256 shares)
      Its 3 indexed params ARE declared first, so declaration order and wire
      order coincide and emitted_arg_N is unambiguous either way.
    * idToMarketParams(bytes32) -> (loanToken, collateralToken, oracle, irm,
      lltv). loanToken (output_arg_0) is the asset `supply()` actually moves.
      Field order independently confirmed by `irm` landing on the documented
      AdaptiveCurveIRM 0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC.
    * position(bytes32,address) -> (supplyShares uint256, borrowShares uint128,
      collateral uint128). Note the non-uniform widths.
    * market(bytes32) -> (totalSupplyAssets, totalSupplyShares,
      totalBorrowAssets, totalBorrowShares, lastUpdate, fee), all uint128.

    NOTE: the Hypernative "Morpho Protocol Configuration Guide" docs page is
    STALE and contradicts all of the above (it names the old Morpho-Aave
    Optimizer 0x0de04a86... as "Main Protocol" and gives a different Supply
    signature and market() ordering). The facts above come from the morpho-blue
    source and live mainnet reads, not from that page.

`SupplyCollateral` IS A DIFFERENT EVENT
    It moves the market's collateralToken (not the loanToken) and mints no
    shares. If collateral deposits need tracking too, add a separate agent
    rather than bending this one.
"""

from invariantive.model import (Agent, AlertConfig, ContextVariable, ContractScoreVariable,
                                EventTrigger, GenericContractReadVariable,
                                PythonProcessingVariable, RunConfig)
from invariantive.model.trigger import Condition

# NOTE: NOTIFICATION_CHANNEL_IDS and SEVERITY read as unused to a linter.
# They are referenced by the commented-out agent.deploy(...) block at the
# bottom of this file. Do not remove them.
from shared.common import (CHAINS, SAFE_LIST_UUID, MORPHO_ARG_ASSETS,
                           MORPHO_ARG_CALLER, MORPHO_ARG_MARKET_ID, MORPHO_ARG_ONBEHALF,
                           MORPHO_ARG_SHARES, MORPHO_TX_CALLER_NE_ONBEHALF,
                           MORPHO_TX_USDC_LARGE, MORPHO_TX_WETH_18DP,
                           NOTIFICATION_CHANNEL_IDS, SEVERITY, detect_chain_for_tx,
                           get_cli_flag, get_cli_tx_hashes, is_quiet_mode,
                           print_findings, progress, progress_done,
                           progress_note)

AGENT_NAME = "Morpho Blue deposit destination (audit trail)"


def build_audit_line(extracted_variables):
    """Compose the audit line for one Morpho Blue supply.

    SANDBOX RULES OBSERVED: constants inside the function (no module globals),
    imports inside the function, no `+=`, no tuple unpacking, no inline
    ternary, no leading-underscore names, f-strings only.
    """
    try:
        safes_list_uuid = "SAFE_LIST_UUID"

        # Read from the trigger's own "emitting_contract" rather than
        # hardcoded: this same function serves every chain's agent (see
        # CHAINS in shared/common.py), and while Morpho Blue's address
        # happens to be identical on Ethereum and Base today, hardcoding it
        # would silently break if that ever stops being true.
        morpho_blue = extracted_variables.get("market_contract")
        # Morpho SharesMathLib constants. Declared here, not imported.
        virtual_shares = 1000000
        virtual_assets = 1

        market_id = extracted_variables.get("market_id")
        safe_address = extracted_variables.get("safe_address")
        caller = extracted_variables.get("caller")
        assets_raw = extracted_variables.get("assets_raw")
        loan_symbol = extracted_variables.get("loan_symbol")
        loan_decimals = extracted_variables.get("loan_decimals")
        collateral_symbol = extracted_variables.get("collateral_symbol")
        lltv_raw = extracted_variables.get("lltv")
        shares_after = extracted_variables.get("supply_shares_after")
        total_assets = extracted_variables.get("total_supply_assets")
        total_shares = extracted_variables.get("total_supply_shares")
        tx_hash = extracted_variables.get("tx_hash")
        block_number = extracted_variables.get("block_number")

        if loan_decimals is None:
            loan_decimals = 18
        if loan_symbol is None:
            loan_symbol = "token"
        if collateral_symbol is None:
            collateral_symbol = "?"

        scale = float(10 ** int(loan_decimals))
        amount = float(int(assets_raw)) / scale
        amount_text = f"{amount:,.6f}".rstrip("0").rstrip(".")
        # A deposit smaller than 0.0000005 rounds to "0" at 6dp. Reporting
        # "Deposit: 0 USDC" for a real transfer of value is a misstatement in an
        # audit record, so fall back to exact base units instead.
        if amount_text == "0" and int(assets_raw) > 0:
            amount_text = f"{int(assets_raw)} base units of"

        # LLTV is an 18-decimal fraction, e.g. 915000000000000000 = 91.5%.
        if lltv_raw is None:
            lltv_text = "?"
        else:
            lltv_text = f"{(float(int(lltv_raw)) / 1e16):.1f}"

        # Shares -> assets with virtual shares. Guard against a zero-share
        # market (a brand-new market) rather than dividing by zero.
        position_text = "unavailable"
        if shares_after is not None:
            shares_int = int(shares_after)
            shares_text = f"{shares_int:,}"
            if total_assets is None or total_shares is None:
                position_text = f"{shares_text} supply shares"
            else:
                denominator = int(total_shares) + virtual_shares
                if denominator > 0:
                    numerator = shares_int * (int(total_assets) + virtual_assets)
                    value_units = numerator // denominator
                    value = float(value_units) / scale
                    value_text = f"{value:,.6f}".rstrip("0").rstrip(".")
                    # Same rounding trap on the converted value.
                    if value_text == "0" and value_units > 0:
                        value_text = f"{value_units} base units of"
                    position_text = (
                        f"{shares_text} supply shares, worth about "
                        f"{value_text} {loan_symbol} as of last accrual"
                    )
                else:
                    position_text = f"{shares_text} supply shares"

        safe_label = ""
        try:
            note = list_client.get_note(safes_list_uuid, safe_address, chain.ethereum)
            if note:
                safe_label = f" ({note})"
        except Exception:
            safe_label = ""

        initiator_note = ""
        mismatch = False
        if caller and safe_address:
            if str(caller).lower() != str(safe_address).lower():
                mismatch = True
                initiator_note = f" NOTE: initiated by {caller}, not the holding Safe."

        # Blank line before "Safe now holds" -- see the same note in
        # agent_aave_v3_supply.py. Part of the real alert text, not just
        # local display.
        description = (
            f"Deposit: {amount_text} {loan_symbol} from Safe {safe_address}{safe_label} "
            f"-> Morpho Blue market {market_id} "
            f"({loan_symbol} lent against {collateral_symbol}, LLTV {lltv_text}%) "
            f"via {morpho_blue}."
            f"\n\n"
            f"Safe now holds {position_text}. "
            f"Tx {tx_hash} @ block {block_number}.{initiator_note}"
        )

        return {
            "audit_line": description,
            "amount": amount,
            "amount_text": amount_text,
            "position_text": position_text,
            "initiator_mismatch": mismatch,
        }
    except Exception as exception:
        return {
            "audit_line": f"Morpho Blue deposit detected but formatting failed: {exception}",
            "amount": 0,
            "amount_text": "?",
            "position_text": "?",
            "initiator_mismatch": False,
        }


def extract_audit_line(extracted_variables):
    """Surface the composed line as a flat variable for the alert template."""
    formatted = extracted_variables.get("formatted")
    if formatted is None:
        return "Morpho Blue deposit detected (formatter produced no output)"
    return formatted.get("audit_line")


def build_agent(chain_key="ethereum", apply_safe_filter=True):
    """Assemble the agent for one chain.

    chain_key selects the chain's Morpho Blue address from CHAINS in
    shared/common.py ("ethereum" or "base") -- it happens to be the same
    address on both today, but is looked up per chain rather than assumed.

    apply_safe_filter=False builds the test shape used by the fixture replay in
    __main__. NEVER deploy the unfiltered shape.
    """
    chain_config = CHAINS[chain_key]
    chain = chain_config["chain"]
    morpho_blue = chain_config["morpho_blue"]

    agent = Agent(
        trigger=EventTrigger(
            chain=chain,
            contract_address=morpho_blue,
            event_sig="Supply",
            output_index="emitting_contract",
            operator="compare_exact",
            operands=[morpho_blue],
        )
    )

    # Safe filter, applied before the expensive contract reads.
    # !! A WRONG LIST UUID FAILS SILENTLY (0 findings, no error) !!
    if apply_safe_filter:
        agent.add_trigger_condition(
            Condition(
                output_index=MORPHO_ARG_ONBEHALF,
                operator="in_lists",
                operands=[SAFE_LIST_UUID],
            )
        )

    # The Morpho Blue address that actually emitted this event -- read
    # dynamically rather than hardcoded in build_audit_line(), since the SAME
    # formatter function is used for every chain's agent.
    agent.add_variable(ContextVariable(output_index="emitting_contract", var_name="market_contract"))
    # Event args and transaction identity.
    agent.add_variable(ContextVariable(output_index=MORPHO_ARG_MARKET_ID, var_name="market_id"))
    agent.add_variable(ContextVariable(output_index=MORPHO_ARG_CALLER, var_name="caller"))
    agent.add_variable(ContextVariable(output_index=MORPHO_ARG_ONBEHALF, var_name="safe_address"))
    agent.add_variable(ContextVariable(output_index=MORPHO_ARG_ASSETS, var_name="assets_raw"))
    agent.add_variable(ContextVariable(output_index=MORPHO_ARG_SHARES, var_name="shares_minted"))
    agent.add_variable(ContextVariable(output_index="tx_hash", var_name="tx_hash"))
    agent.add_variable(ContextVariable(output_index="block_number", var_name="block_number"))
    agent.add_variable(ContextVariable(output_index="block_timestamp", var_name="block_timestamp"))
    agent.add_variable(ContextVariable(output_index="tx_from", var_name="executed_by"))

    # ----------------------------------------------------------------------
    # Resolve the market. The Supply event carries only the bytes32 id, so the
    # actual token identities have to be looked up. Morpho Blue is verified and
    # not a proxy, so the simple func_sig form resolves.
    # ----------------------------------------------------------------------
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="idToMarketParams",
            input=["extracted_variables.market_id"],
            output_index="output_arg_0", var_name="loan_token",       # the asset supplied
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="idToMarketParams",
            input=["extracted_variables.market_id"],
            output_index="output_arg_1", var_name="collateral_token",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="idToMarketParams",
            input=["extracted_variables.market_id"],
            output_index="output_arg_4", var_name="lltv",
        )
    )

    # Token metadata. Dynamic addresses, so full signature + ABI types required.
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address="extracted_variables.loan_token",
            func_sig="decimals()", input=[], input_data_type=[],
            output_data_type=["uint8"], output_index="output_arg_0",
            var_name="loan_decimals",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address="extracted_variables.loan_token",
            func_sig="symbol()", input=[], input_data_type=[],
            output_data_type=["string"], output_index="output_arg_0",
            var_name="loan_symbol",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address="extracted_variables.collateral_token",
            func_sig="symbol()", input=[], input_data_type=[],
            output_data_type=["string"], output_index="output_arg_0",
            var_name="collateral_symbol",
        )
    )

    # ----------------------------------------------------------------------
    # The position after the deposit. Morpho has no receipt ERC-20: the claim
    # is an internal supplyShares balance, so it is read from the protocol
    # rather than via TokenBalanceVariable.
    # ----------------------------------------------------------------------
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="position",
            input=["extracted_variables.market_id", "extracted_variables.safe_address"],
            output_index="output_arg_0", var_name="supply_shares_after",
        )
    )
    # Market totals, needed to convert shares back into the underlying asset.
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="market",
            input=["extracted_variables.market_id"],
            output_index="output_arg_0", var_name="total_supply_assets",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain, contract_address=morpho_blue, func_sig="market",
            input=["extracted_variables.market_id"],
            output_index="output_arg_1", var_name="total_supply_shares",
        )
    )

    # Risk scores -- LOCAL DEMO ONLY, gated on the test shape so the deployed
    # agent and exported rule JSON stay free of them. See the equivalent block
    # in agent_aave_v3_supply.py for the full reasoning.
    if not apply_safe_filter:
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.loan_token",
                var_name="loan_token_risk_score",
            )
        )
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.collateral_token",
                var_name="collateral_token_risk_score",
            )
        )
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.market_contract",
                var_name="market_contract_risk_score",
            )
        )

    agent.add_variable(
        PythonProcessingVariable(source_code=build_audit_line, var_name="formatted")
    )
    agent.add_variable(
        PythonProcessingVariable(source_code=extract_audit_line, var_name="audit_line")
    )

    # Every deposit by a monitored Safe is reportable, so there is no threshold.
    agent.set_alert(
        AlertConfig(
            var_name="audit_line", operator="any", operands=[],
            description="{{audit_line}}",
            involved_assets={
                "safe_address": "Monitored Safe",
                "loan_token": "Asset deposited",
                "collateral_token": "Market collateral",
            },
        )
    )
    return agent


# One agent per chain in CHAINS -- see the module docstring's "BASE SUPPORT
# AND THE SAME-ADDRESS QUIRK" for why each chain gets its own agent/rule file
# even though the contract address is identical across them.
agents = {}
for _build_index, chain_key in enumerate(CHAINS, 1):
    # Seconds of ABI validation per chain, silent otherwise.
    progress("Building agents", _build_index, len(CHAINS))
    agents[chain_key] = build_agent(chain_key=chain_key, apply_safe_filter=True)
    agents[chain_key].save_config(f"rules/rule_morpho_blue_supply_{chain_key}.json")
progress_done()


# ==========================================================================
# DEPLOY -- intentionally commented out. See README.md for the pre-deploy
# checklist (channel id, list UUID, chains in scope, custom-agent quota --
# this now deploys one agent PER CHAIN in CHAINS, so it counts double).
# Channel id: Actions > Notification Channels > open channel > id in the URL,
# or GET https://api.hypernative.xyz/notification-channels
# (headers: x-client-id, x-client-secret).
# ==========================================================================
# for chain_key, chain_agent in agents.items():
#     agent_id = chain_agent.deploy(
#         agent_name=f"{AGENT_NAME} ({chain_key})",
#         severity=SEVERITY,                                  # "Info"
#         channels_configurations=NOTIFICATION_CHANNEL_IDS,   # e.g. [{"id": 1337}]
#     )
#     print(f"deployed {chain_key} agent id: {agent_id}")


if __name__ == "__main__":
    # Real historical Morpho Blue supplies -- either the built-in
    # verification fixtures (Ethereum only), or one or more tx hashes passed
    # on the command line to replay a specific real deposit:
    #   python3 agent_morpho_blue_supply.py 0xTxHash [0xTxHash2 ...] [--chain=base] [--quiet]
    #
    # The fixtures/custom hashes are not necessarily monitored Safes, so the
    # unfiltered shape is used to exercise the full path; the production
    # `agents` above keep the Safe filter.
    #
    # --quiet / -q : print only the ALERT lines, no debug variable dump.
    # Demo-friendly for screen-sharing with a customer.
    quiet = is_quiet_mode()
    chain_key = get_cli_flag("chain")
    custom_hashes = get_cli_tx_hashes()

    # See the equivalent block in agent_aave_v3_supply.py: a tx hash doesn't
    # say which chain it is from, so find out rather than failing on the
    # default. An explicit --chain= always wins.
    if chain_key is None and custom_hashes:
        progress_note("detecting which chain this transaction is on...")
        chain_key = detect_chain_for_tx(custom_hashes[0])
        progress_done()
        if chain_key is None:
            print(f"Transaction {custom_hashes[0]} was not found on any configured "
                  f"chain ({', '.join(CHAINS)}).")
            print("Check the hash, or add the chain to CHAINS in shared/common.py.")
            raise SystemExit(1)
        if not quiet:
            print(f"(auto-detected chain: {chain_key} -- pass --chain= to override)")
    if chain_key is None:
        chain_key = "ethereum"

    progress_note("building agent...")
    test_agent = build_agent(chain_key=chain_key, apply_safe_filter=False)
    progress_done()

    if custom_hashes:
        fixtures = [(tx_hash, "custom tx") for tx_hash in custom_hashes]
    else:
        fixtures = [
            (MORPHO_TX_USDC_LARGE, "355,036.521182 USDC, caller == onBehalf"),
            (MORPHO_TX_WETH_18DP, "0.001 WETH, 18-decimal case"),
            (MORPHO_TX_CALLER_NE_ONBEHALF, "1,007.278549 USDC via router, caller != onBehalf"),
        ]
    # Each run() is seconds of market reads with nothing on screen.
    for index, (tx_hash, label) in enumerate(fixtures, 1):
        progress("Replaying", index, len(fixtures))
        result = test_agent.run(RunConfig(chain=CHAINS[chain_key]["chain"], hashes=[tx_hash]))
        progress_done()
        print_findings(result, f"Morpho Blue ({chain_key}): {label}", quiet=quiet)
