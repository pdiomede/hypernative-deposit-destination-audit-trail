"""
probe_aave_args.py -- resolve, empirically, how Hypernative numbers the
`emitted_arg_N` indices for Aave v3's `Supply` event.

WHY THIS EXISTS
---------------
Aave v3 declares Supply with its indexed params out of order:

    event Supply(address indexed reserve, address user,
                 address indexed onBehalfOf, uint256 amount,
                 uint16 indexed referralCode)

So the on-the-wire log layout (topics = reserve, onBehalfOf, referralCode;
data = user, amount) does NOT match the declaration order. Two mappings were
therefore possible, and every example in the Hypernative docs happens to declare
its indexed params first, so none of them can tell the two apart:

    index            declaration order      topics-then-data order
    emitted_arg_1    user                   onBehalfOf
    emitted_arg_2    onBehalfOf             referralCode
    emitted_arg_3    amount                 user

Picking wrong means the Safe filter matches on the wrong field and the agent
silently never fires. So we do not guess: we run the real thing against a real
transaction where the values are all distinguishable.

RESULT (run 2026-09-07, mainnet)
--------------------------------
    arg0 = 0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2   WETH             -> reserve
    arg1 = 0xd01607c3c5ecaba394d8be377a08590149325722   WrappedTokenGateway -> user
    arg2 = 0x029a0f3afe803e6c6f45773b37fd1649640d650f                    -> onBehalfOf
    arg3 = 81000000000000000                            0.081 WETH       -> amount
    arg4 = 0                                                             -> referralCode

=> Hypernative numbers emitted_arg_N by DECLARATION order, indexed args included.
=> The Safe filter belongs on emitted_arg_2 (onBehalfOf).

This run also confirmed two other open questions:
  * the simple `event_sig="Supply"` DOES resolve even though the Pool is an
    EIP-1967 proxy whose own verified ABI is the proxy's, so the full-signature
    fallback is not needed;
  * the context field is `tx_hash` (not `txhash`).

Re-run this if Aave ever changes the event, or to re-verify after an SDK upgrade.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from invariantive.common.consts import Chain
from invariantive.model import (Agent, AlertConfig, ContextVariable, EventTrigger,
                                RunConfig)

from shared.common import AAVE_TX_USER_NE_ONBEHALF, AAVE_V3_POOL, CHAIN, print_findings

# Deliberately NO Safe filter here: we want the event to come through so we can
# read the raw arg positions.
agent = Agent(
    trigger=EventTrigger(
        chain=CHAIN,
        contract_address=AAVE_V3_POOL,
        event_sig="Supply",
        output_index="emitting_contract",
        operator="compare_exact",
        operands=[AAVE_V3_POOL],
    )
)

for index in range(5):
    agent.add_variable(
        ContextVariable(output_index=f"emitted_arg_{index}", var_name=f"arg{index}")
    )

# Confirm the spelling of the tx-hash context field while we are here.
agent.add_variable(ContextVariable(output_index="tx_hash", var_name="tx_hash"))

# operator="any" so the alert always fires. Extracted variables are only
# returned on a firing alert, so this is required for a probe.
agent.set_alert(
    AlertConfig(
        var_name="arg0",
        operator="any",
        operands=[],
        description=(
            "PROBE arg0={{arg0}} arg1={{arg1}} arg2={{arg2}} "
            "arg3={{arg3}} arg4={{arg4}} tx={{tx_hash}}"
        ),
    )
)

if __name__ == "__main__":
    result = agent.run(RunConfig(chain=Chain.ethereum, hashes=[AAVE_TX_USER_NE_ONBEHALF]))
    print_findings(result, "PROBE: Aave v3 Supply emitted_arg_N mapping")
    print("\nExpected, if numbering is by DECLARATION order:")
    print("  arg1 = 0xd01607c3... (gateway = `user`)")
    print("  arg2 = 0x029a0f3a... (`onBehalfOf`)")
    print("  arg3 = 81000000000000000 (`amount`)")
