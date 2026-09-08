"""
Aave v3 deposit destination (audit trail)

Fires on every Aave v3 `Supply` on a supported chain (Ethereum or Base, see
CHAINS in shared/common.py) where the aTokens land on a monitored Safe, and
emits one plain-English audit line answering "where did the money go".

Base support: the Pool address differs per chain (unlike Morpho Blue, whose
address happens to be identical on both -- see agent_morpho_blue_supply.py),
so the alert text's own Pool address already disambiguates which chain fired.
Verified via Basescan (Exact Match) but not yet confirmed by replaying a real
Base transaction -- there is no Base fixture below, only Ethereum ones.

WHAT THE ALERT CONTAINS
    protocol (Aave v3) · destination reserve named by asset, with the Pool
    address and the aToken that custodies the underlying · the asset and
    decimal-adjusted amount · the aToken symbol and the Safe's aToken balance
    after the deposit · the initiating Safe · the tx hash and block

WHY THE FILTER IS ON onBehalfOf
    `onBehalfOf` receives the aTokens, so it is who actually holds the position
    afterwards. That is the honest answer to "where are our funds held".
      * Filtering on `tx_from` would be WRONG: a Safe multisig execution has
        tx_from = the executing owner's EOA, not the Safe, so the agent would
        never fire.
      * Filtering on `user` (the caller) would miss deposits routed through a
        gateway or router, a real and common pattern. See the fixture
        AAVE_TX_USER_NE_ONBEHALF, where the caller is Aave's own
        WrappedTokenGatewayV3.
    When `user` != `onBehalfOf` the alert says so: that is a material audit
    fact, because something other than the holding Safe initiated the deposit.

VERIFIED FACTS THIS FILE DEPENDS ON
    * emitted_arg_N numbering: resolved empirically by replaying a real tx
      where `user` and `onBehalfOf` differ (see CHANGELOG.md 0.0.1).
      Hypernative numbers by DECLARATION order, indexed args included. See
      shared/common.py for the recorded probe output.
    * aTokens mint to onBehalfOf, and the aToken address is NOT in the event,
      so it must be read from the Pool.
    * Pool.getReserveAToken(address) is live on mainnet (Aave v3.4+; on-chain
      POOL_REVISION() == 11 => v3.7.0) and returns the aToken in one word.
      Cross-checked against getReserveData()[8]: both returned
      0x4d5f47fa...514e8 = aEthWETH for WETH.
    * TokenBalanceVariable honours "extracted_variables.*" for BOTH
      token_address and token_holder_address, and reads state at the TRIGGERING
      BLOCK (verified: it returned the block-25926408 balance, not head), so
      "balance after this deposit" is accurate even when replaying history.
"""

from invariantive.model import (Agent, AlertConfig, ContextVariable, ContractScoreVariable,
                                EventTrigger, GenericContractReadVariable,
                                PythonProcessingVariable, RunConfig, TokenBalanceVariable)
from invariantive.model.trigger import Condition

# NOTE: NOTIFICATION_CHANNEL_IDS and SEVERITY read as unused to a linter.
# They are referenced by the commented-out agent.deploy(...) block at the
# bottom of this file. Do not remove them.
from shared.common import (AAVE_ARG_AMOUNT, AAVE_ARG_ONBEHALFOF, AAVE_ARG_RESERVE,
                           AAVE_ARG_USER, AAVE_EVENT, AAVE_TX_USDC_SMALL,
                           AAVE_TX_USDT_SIMPLE, AAVE_TX_USER_NE_ONBEHALF,
                           CHAINS, SAFE_LIST_UUID, NOTIFICATION_CHANNEL_IDS,
                           SEVERITY, get_cli_flag, get_cli_tx_hashes,
                           is_quiet_mode, print_findings)

AGENT_NAME = "Aave v3 deposit destination (audit trail)"


# ==========================================================================
# The formatter. Kept at module level so the SDK can lift its source cleanly.
#
# SANDBOX RULES OBSERVED (this runs in RestrictedPython on the platform, which
# is stricter than the local agent.run()):
#   * every constant declared INSIDE the function, no closure over module
#     globals (that works locally and then raises NameError in production)
#   * imports inside the function
#   * no augmented assignment (+=), no tuple unpacking, no inline ternary
#   * no variable name starting with an underscore
#   * f-strings for string building, never mixed implicit/`+` concatenation
# ==========================================================================
def build_audit_line(extracted_variables):
    """Compose the human-readable audit line for one Aave v3 supply."""
    try:
        # Constants live inside the function: the sandbox gives this code no
        # access to module-level names.
        safes_list_uuid = "SAFE_LIST_UUID"

        # Read from the trigger's own "emitting_contract" rather than
        # hardcoded, since this same function serves both the Ethereum and
        # Base agents (see CHAINS in shared/common.py) -- a literal here
        # would print the wrong Pool address on whichever chain isn't first.
        pool_address = extracted_variables.get("pool_address")

        safe_address = extracted_variables.get("safe_address")
        caller_user = extracted_variables.get("caller_user")
        amount_raw = extracted_variables.get("amount_raw")
        asset_decimals = extracted_variables.get("asset_decimals")
        asset_symbol = extracted_variables.get("asset_symbol")
        atoken = extracted_variables.get("atoken")
        atoken_symbol = extracted_variables.get("atoken_symbol")
        atoken_balance = extracted_variables.get("atoken_balance")
        tx_hash = extracted_variables.get("tx_hash")
        block_number = extracted_variables.get("block_number")

        if asset_decimals is None:
            asset_decimals = 18
        if asset_symbol is None:
            asset_symbol = "token"
        if atoken_symbol is None:
            atoken_symbol = "aToken"

        # Decimal-adjust. Never put raw units in an audit line: "5000000" and
        # "5 USDC" are not the same statement.
        amount = float(int(amount_raw)) / float(10 ** int(asset_decimals))
        amount_text = f"{amount:,.6f}".rstrip("0").rstrip(".")
        # A deposit smaller than 0.0000005 rounds to "0" at 6dp. Reporting
        # "Deposit: 0 USDC" for a real transfer of value is a misstatement in an
        # audit record, so fall back to exact base units instead.
        if amount_text == "0" and int(amount_raw) > 0:
            amount_text = f"{int(amount_raw)} base units of"

        if atoken_balance is None:
            balance_text = "unavailable"
        else:
            balance_text = f"{float(atoken_balance):,.6f}".rstrip("0").rstrip(".")
            # Same rounding trap on the balance side.
            if balance_text == "0" and float(atoken_balance) > 0:
                balance_text = f"{float(atoken_balance):.18f}".rstrip("0")

        # The Safe's human label, maintained as the `note` on the same List that
        # drives this agent's filter. Optional: never let it break the alert.
        safe_label = ""
        try:
            note = list_client.get_note(safes_list_uuid, safe_address, chain.ethereum)
            if note:
                safe_label = f" ({note})"
        except Exception:
            safe_label = ""

        # Material audit fact: initiated by something other than the holder.
        initiator_note = ""
        mismatch = False
        if caller_user and safe_address:
            if str(caller_user).lower() != str(safe_address).lower():
                mismatch = True
                initiator_note = f" NOTE: initiated by {caller_user}, not the holding Safe."

        # The blank line before "Safe now holds" is deliberate: it splits the
        # alert into "what moved" and "what the Safe holds now", which is the
        # natural reading break. It is part of the real alert text, so Slack
        # and email get the same two paragraphs the local view shows.
        description = (
            f"Deposit: {amount_text} {asset_symbol} from Safe {safe_address}{safe_label} "
            f"-> Aave v3 {asset_symbol} reserve (Pool {pool_address}, aToken {atoken})."
            f"\n\n"
            f"Safe now holds {balance_text} {atoken_symbol}. "
            f"Tx {tx_hash} @ block {block_number}.{initiator_note}"
        )

        return {
            "audit_line": description,
            "amount": amount,
            "amount_text": amount_text,
            "balance_text": balance_text,
            "initiator_mismatch": mismatch,
        }
    except Exception as exception:
        # Never fail silently: surface the problem in the alert itself, so a
        # broken agent is visible rather than merely quiet.
        return {
            "audit_line": f"Aave v3 deposit detected but formatting failed: {exception}",
            "amount": 0,
            "amount_text": "?",
            "balance_text": "?",
            "initiator_mismatch": False,
        }


def extract_audit_line(extracted_variables):
    """Lift the composed line into a flat variable.

    AlertConfig.description can only interpolate {{var_name}}, never
    {{obj.field}}, so the nested value has to be surfaced on its own.
    """
    formatted = extracted_variables.get("formatted")
    if formatted is None:
        return "Aave v3 deposit detected (formatter produced no output)"
    return formatted.get("audit_line")


def build_agent(chain_key="ethereum", apply_safe_filter=True):
    """Assemble the agent for one chain.

    chain_key selects the chain's Pool address from CHAINS in
    shared/common.py ("ethereum" or "base").

    apply_safe_filter=True  -> production shape, only monitored Safes.
    apply_safe_filter=False -> test shape, every Aave v3 supply, so the fixture
                               replay in __main__ can exercise the full
                               extraction and formatting path against real
                               historical transactions.
                               NEVER DEPLOY the unfiltered shape: it would
                               alert on every Aave deposit on the chain.
    """
    chain_config = CHAINS[chain_key]
    chain = chain_config["chain"]
    pool_address = chain_config["aave_pool"]

    # ----------------------------------------------------------------------
    # 1. TRIGGER: any Supply emitted by the Aave v3 Pool. Pinning
    #    output_index="emitting_contract" to the Pool guarantees we only react
    #    to the real Pool, not to some other contract's same-named event.
    # ----------------------------------------------------------------------
    agent = Agent(
        trigger=EventTrigger(
            chain=chain,
            contract_address=pool_address,
            # The simple name is enough: verified that the SDK resolves
            # `Supply` through the EIP-1967 proxy, even though the Pool
            # address's own verified ABI is the proxy's. If a future SDK stops
            # following the proxy this raises
            #   ValueError: event Supply not found in abi
            # at build time. Fallback in that case:
            #   event_sig="Supply(address,address,address,uint256,uint16)",
            #   output_data_type=["address","address","address","uint256","uint16"],
            event_sig=AAVE_EVENT,
            output_index="emitting_contract",
            operator="compare_exact",
            operands=[pool_address],
        )
    )

    # ----------------------------------------------------------------------
    # 2. CONDITION: restrict to monitored Safes BEFORE any variable extraction.
    #    Aave's Pool is busy and the contract reads below are the expensive
    #    part, so this filter is what lets the agent keep up with chain speed.
    #
    #    Driving it from a Hypernative List means you add a new Safe in the
    #    UI and every agent referencing that List picks it up. No code change,
    #    no redeploy.
    #
    #    !! A WRONG LIST UUID FAILS SILENTLY !! Verified: an unrecognised UUID
    #    produces 0 findings and no error, which is indistinguishable from
    #    "no deposits happened". Confirm the UUID before trusting quiet.
    # ----------------------------------------------------------------------
    if apply_safe_filter:
        agent.add_trigger_condition(
            Condition(
                output_index=AAVE_ARG_ONBEHALFOF,
                operator="in_lists",
                operands=[SAFE_LIST_UUID],
            )
        )

    # ----------------------------------------------------------------------
    # 3. CONTEXT: the event args plus the transaction identity.
    # ----------------------------------------------------------------------
    # The Pool address that actually emitted this event -- read dynamically
    # rather than hardcoded in build_audit_line(), since the SAME formatter
    # function is used for every chain's agent (see CHAINS in shared/common.py).
    agent.add_variable(ContextVariable(output_index="emitting_contract", var_name="pool_address"))
    agent.add_variable(ContextVariable(output_index=AAVE_ARG_RESERVE, var_name="reserve"))
    agent.add_variable(ContextVariable(output_index=AAVE_ARG_USER, var_name="caller_user"))
    agent.add_variable(ContextVariable(output_index=AAVE_ARG_ONBEHALFOF, var_name="safe_address"))
    agent.add_variable(ContextVariable(output_index=AAVE_ARG_AMOUNT, var_name="amount_raw"))
    agent.add_variable(ContextVariable(output_index="tx_hash", var_name="tx_hash"))
    agent.add_variable(ContextVariable(output_index="block_number", var_name="block_number"))
    agent.add_variable(ContextVariable(output_index="block_timestamp", var_name="block_timestamp"))
    # The EOA that executed the Safe transaction: names the human who pressed
    # the button, as distinct from the Safe itself.
    agent.add_variable(ContextVariable(output_index="tx_from", var_name="executed_by"))

    # ----------------------------------------------------------------------
    # 4. RESOLVE THE ASSET. The reserve is only known at runtime, so these use
    #    a dynamic contract_address, which REQUIRES the full function signature
    #    plus input/output ABI types: the ABI cannot be fetched for an address
    #    that is unknown until the event arrives.
    # ----------------------------------------------------------------------
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain,
            contract_address="extracted_variables.reserve",
            func_sig="decimals()",
            input=[],
            input_data_type=[],
            output_data_type=["uint8"],
            output_index="output_arg_0",
            var_name="asset_decimals",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain,
            contract_address="extracted_variables.reserve",
            func_sig="symbol()",
            input=[],
            input_data_type=[],
            output_data_type=["string"],
            output_index="output_arg_0",
            var_name="asset_symbol",
        )
    )

    # ----------------------------------------------------------------------
    # 5. RESOLVE THE RECEIPT TOKEN (the aToken).
    #    The Supply event does not carry it, so read it from the Pool.
    #    getReserveAToken is the stable one-word read.
    #
    #    Fallback if it ever disappears: getReserveData(asset) -> output_arg_8.
    #    That works today too (both verified to return the same address), but it
    #    depends on the 15-field ReserveDataLegacy tuple keeping aTokenAddress
    #    at index 8, so it is the more brittle of the two.
    #
    #    The Pool address is static, so the simple func_sig form is fine.
    # ----------------------------------------------------------------------
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain,
            contract_address=pool_address,
            func_sig="getReserveAToken",
            input=["extracted_variables.reserve"],
            output_index="output_arg_0",
            var_name="atoken",
        )
    )
    agent.add_variable(
        GenericContractReadVariable(
            chain=chain,
            contract_address="extracted_variables.atoken",
            func_sig="symbol()",
            input=[],
            input_data_type=[],
            output_data_type=["string"],
            output_index="output_arg_0",
            var_name="atoken_symbol",
        )
    )

    # ----------------------------------------------------------------------
    # 6. THE POST-DEPOSIT BALANCE. scale_to_decimals=True returns a
    #    human-readable number. aTokens share the underlying's decimals, so
    #    there is no offset trap here (unlike the Morpho vaults).
    # ----------------------------------------------------------------------
    agent.add_variable(
        TokenBalanceVariable(
            chain=chain,
            token_address="extracted_variables.atoken",
            token_holder_address="extracted_variables.safe_address",
            scale_to_decimals=True,
            var_name="atoken_balance",
        )
    )

    # ----------------------------------------------------------------------
    # 6b. RISK SCORES -- LOCAL DEMO ONLY, deliberately not deployed.
    #     Hypernative's per-contract reputation score, shown in the grouped
    #     CLI output. Gated on the test shape so the deployed agent and the
    #     exported rule JSON stay free of them: they cost extra calls per
    #     event in production and the alert text never references them.
    #     -1 means "not scored yet" -- see print_variables() in
    #     shared/common.py for how that's rendered.
    # ----------------------------------------------------------------------
    if not apply_safe_filter:
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.reserve",
                var_name="reserve_risk_score",
            )
        )
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.atoken",
                var_name="atoken_risk_score",
            )
        )
        agent.add_variable(
            ContractScoreVariable(
                chain=chain, contract_address="extracted_variables.pool_address",
                var_name="pool_risk_score",
            )
        )

    # ----------------------------------------------------------------------
    # 7. FORMAT.
    # ----------------------------------------------------------------------
    agent.add_variable(
        PythonProcessingVariable(source_code=build_audit_line, var_name="formatted")
    )
    agent.add_variable(
        PythonProcessingVariable(source_code=extract_audit_line, var_name="audit_line")
    )

    # ----------------------------------------------------------------------
    # 8. ALERT. operator="any" because the trigger plus the Safe-list condition
    #    ARE the predicate: every deposit by a monitored Safe is a reportable
    #    audit event, so there is no threshold to apply. EventTrigger alerts
    #    default to one-shot, correct here since each deposit is discrete.
    # ----------------------------------------------------------------------
    agent.set_alert(
        AlertConfig(
            var_name="audit_line",
            operator="any",
            operands=[],
            description="{{audit_line}}",
            involved_assets={
                "safe_address": "Monitored Safe",
                "reserve": "Asset deposited",
                "atoken": "Receipt token (aToken)",
            },
        )
    )
    return agent


# Production shape, and the rule export -- one agent per chain in CHAINS.
# save_config() runs at module level, ABOVE the __main__ guard, so the JSON
# is produced even if a test run errors.
agents = {}
for chain_key in CHAINS:
    agents[chain_key] = build_agent(chain_key=chain_key, apply_safe_filter=True)
    agents[chain_key].save_config(f"rules/rule_aave_v3_supply_{chain_key}.json")


# ==========================================================================
# 9. DEPLOY -- intentionally commented out.
#
# Before uncommenting, confirm in your account:
#   1. NOTIFICATION_CHANNEL_IDS in shared/common.py. Get the id from
#      Actions > Notification Channels > open the channel > id in the URL, or
#      GET https://api.hypernative.xyz/notification-channels
#      (headers: x-client-id, x-client-secret).
#   2. SAFE_LIST_UUID in shared/common.py points at a real List of the Safes you
#      want to monitor. A wrong UUID gives a permanently silent agent.
#   3. Custom-agent quota: check your plan's limit -- this now deploys one
#      agent PER CHAIN in CHAINS, so it counts double against that quota.
#
# A newly created custom agent takes up to 3 minutes to become active, so an
# immediate smoke test can look like a false negative.
# ==========================================================================
# for chain_key, chain_agent in agents.items():
#     agent_id = chain_agent.deploy(
#         agent_name=f"{AGENT_NAME} ({chain_key})",
#         severity=SEVERITY,                                  # "Info"
#         channels_configurations=NOTIFICATION_CHANNEL_IDS,   # e.g. [{"id": 1337}]
#     )
#     print(f"deployed {chain_key} agent id: {agent_id}")


if __name__ == "__main__":
    # Replay against REAL historical Aave v3 supply txs -- either the
    # built-in verification fixtures (Ethereum only), or one or more tx
    # hashes passed on the command line to replay a specific real deposit:
    #   python3 agent_aave_v3_supply.py 0xTxHash [0xTxHash2 ...] [--chain=base] [--quiet]
    #
    # The fixtures/custom hashes are not necessarily monitored Safes, so we
    # build the UNFILTERED shape here to exercise the full extraction and
    # formatting path. The production `agents` above (with the Safe filter)
    # are what save_config/deploy use.
    #
    # --quiet / -q : print only the ALERT lines, no debug variable dump.
    # Demo-friendly for screen-sharing with a customer.
    quiet = is_quiet_mode()
    chain_key = get_cli_flag("chain", default="ethereum")
    custom_hashes = get_cli_tx_hashes()
    test_agent = build_agent(chain_key=chain_key, apply_safe_filter=False)

    if custom_hashes:
        fixtures = [(tx_hash, "custom tx") for tx_hash in custom_hashes]
    else:
        fixtures = [
            (AAVE_TX_USDT_SIMPLE, "10,000 USDT, user == onBehalfOf"),
            (AAVE_TX_USER_NE_ONBEHALF, "0.081 WETH via WrappedTokenGateway, user != onBehalfOf"),
            (AAVE_TX_USDC_SMALL, "10.513985 USDC, user == onBehalfOf"),
        ]
    for tx_hash, label in fixtures:
        result = test_agent.run(RunConfig(chain=CHAINS[chain_key]["chain"], hashes=[tx_hash]))
        print_findings(result, f"Aave v3 ({chain_key}): {label}", quiet=quiet)
