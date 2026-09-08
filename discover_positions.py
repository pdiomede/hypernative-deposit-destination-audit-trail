"""
discover_positions.py -- read-only pre-flight. Answers "which Morpho vaults and
Aave v3 reserves do your Safes actually hold?" so you build agents for real
positions instead of guessing.

WHY THIS MATTERS
    A Morpho Vault agent needs a concrete vault address (an EventTrigger takes
    one contract), and every monitored contract counts against your plan's
    pools/contracts limit. Building agents for vaults you do not use burns
    quota for nothing.

WHAT IT DOES
    For each Safe, on EVERY chain in CHAINS (shared/common.py -- Ethereum and
    Base): reads Aave v3 getUserAccountData plus per-reserve aToken balances.
    Morpho vault balances are checked on Ethereum only (MORPHO_VAULT_UNIVERSE
    has no Base entries yet). Nothing is written anywhere. The balance sweep
    talks to each chain's public RPC, not to Hypernative, so it needs no
    Hypernative credentials and consumes no quota.

RPC ENDPOINTS
    The public defaults rate-limit under this script's call pattern (~200
    sequential reads for a full Aave sweep), which surfaces as reserves
    reported as "could not be checked". Two mitigations: failed reads are
    retried with a short backoff, and ETHEREUM_RPC_URL / BASE_RPC_URL in
    config.env point the sweep at your own endpoint. Those URLs normally
    embed an API key, so they are only ever printed by host name.

POOL TOXICITY (optional, off unless credentials exist)
    With HYPERNATIVE_CLIENT_ID / HYPERNATIVE_CLIENT_SECRET in config.env,
    each position found is also screened with Hypernative's Pool Toxicity
    API: how much of the pool's liquidity traces back to sanctioned,
    mixer-linked or hack-proceeds funds. The identifier that API wants is
    exactly what this script already has -- the aToken address for Aave v3,
    the vault address for Morpho Vaults. Without credentials nothing is
    sent anywhere and the output is unchanged. `--no-toxicity` skips it.

USAGE
    1. Put the Safe addresses you want to check in SAFES_TO_CHECK below (or
       pass them as command-line arguments).
    2. python3 discover_positions.py [0xSafe1 0xSafe2 ...] [--no-toxicity]
    3. Copy the vaults it reports into MORPHO_VAULTS_IN_SCOPE in shared/common.py.
"""

import os
import sys
import time

from web3 import Web3

from shared.common import (CHAINS, MORPHO_VAULT_UNIVERSE, chain_rpc_url,
                           describe_rpc_url, print_pool_toxicity, progress,
                           progress_done, progress_note, resolve_toxicity)

# Public RPCs rate-limit (HTTP 429) under the burst of calls a full sweep
# makes -- ~200 sequential reads for 67 Aave reserves. Those failures are
# transient, not real: a reserve reported as "could not be checked" returns
# a balance fine when queried on its own moments later. Retrying with a
# short backoff recovers most of them; setting <CHAIN>_RPC_URL in config.env
# to a paid endpoint avoids them in the first place.
RPC_ATTEMPTS = 3
RPC_BACKOFF_SECONDS = 0.5


def with_retry(operation):
    """Run a single RPC read, retrying transient failures before giving up.

    Re-raises the last exception if every attempt fails, so a genuinely
    unreachable reserve is still reported rather than silently passed over.
    """
    last_exception = None
    for attempt in range(RPC_ATTEMPTS):
        try:
            return operation()
        except Exception as exception:
            last_exception = exception
            if attempt < RPC_ATTEMPTS - 1:
                time.sleep(RPC_BACKOFF_SECONDS * (2 ** attempt))
    raise last_exception

# PLACEHOLDER -- the Safe addresses you want to check. Also accepted as argv.
SAFES_TO_CHECK = []

ERC20_ABI = [
    {"inputs": [{"type": "address"}], "name": "balanceOf",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"type": "string"}],
     "stateMutability": "view", "type": "function"},
]

VAULT_ABI = ERC20_ABI + [
    {"inputs": [{"type": "uint256"}], "name": "convertToAssets",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "asset", "outputs": [{"type": "address"}],
     "stateMutability": "view", "type": "function"},
]

AAVE_POOL_ABI = [
    {"inputs": [{"type": "address"}], "name": "getUserAccountData",
     "outputs": [{"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"},
                 {"type": "uint256"}, {"type": "uint256"}, {"type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "getReservesList",
     "outputs": [{"type": "address[]"}], "stateMutability": "view", "type": "function"},
    {"inputs": [{"type": "address"}], "name": "getReserveAToken",
     "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
]


def check_morpho_vaults(w3, safe, chain_key=None, toxicity=None):
    """Report which of the KNOWN Morpho vaults this Safe holds shares in.

    Deliberately not "every vault": there is no registry lookup here, just a
    fixed list (MORPHO_VAULT_UNIVERSE), so this can only ever report on the
    vaults in that list. Aave reserves, by contrast, are enumerated live
    from the Pool. The wording throughout says which was checked so a blank
    result is never mistaken for "holds no vaults".
    """
    total_vaults = len(MORPHO_VAULT_UNIVERSE)
    print(f"\n  Morpho vaults (checking {total_vaults} known vaults):")
    found = []
    for index, vault in enumerate(MORPHO_VAULT_UNIVERSE, 1):
        progress("Morpho vaults", index, total_vaults)
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(vault["address"]), abi=VAULT_ABI
            )
            shares = with_retry(lambda: contract.functions.balanceOf(safe).call())
            if shares > 0:
                vault_decimals = with_retry(lambda: contract.functions.decimals().call())
                assets = with_retry(lambda: contract.functions.convertToAssets(shares).call())

                # Two different scales, deliberately: shares use the VAULT's
                # decimals, assets use the UNDERLYING ASSET's. Both are read
                # on-chain rather than taken from MORPHO_VAULT_UNIVERSE, so a
                # hand-added vault entry with only address/symbol still works.
                underlying = contract.functions.asset().call()
                asset_contract = w3.eth.contract(address=underlying, abi=ERC20_ABI)
                asset_decimals = asset_contract.functions.decimals().call()
                asset_symbol = asset_contract.functions.symbol().call()

                shares_human = shares / (10 ** vault_decimals)
                assets_human = assets / (10 ** asset_decimals)
                symbol = vault.get("symbol", vault["address"][:10])
                version = vault.get("version", "?")
                progress_done()
                print(f"    HOLDS  {symbol:<12} {version:<5} "
                      f"{shares_human:>18,.6f} shares  ~ "
                      f"{assets_human:>16,.2f} {asset_symbol}")
                found.append({"address": vault["address"], "symbol": symbol})
                if toxicity:
                    # Morpho Vaults: the vault address IS the poolId.
                    print_pool_toxicity(vault["address"], chain_key, toxicity, "      ")
        except Exception as exception:
            label = vault.get("symbol", vault.get("address", "?"))
            progress_done()
            print(f"    error  {label:<12} {exception}")
    progress_done()
    if not found:
        print(f"    (none of the {total_vaults} known vaults held -- "
              f"vaults outside this list are not checked)")
    return found


def check_aave(w3, safe, pool_address, chain_key=None, toxicity=None):
    """Report the Safe's Aave v3 position and which reserves it supplies."""
    print("\n  Aave v3:")
    pool = w3.eth.contract(
        address=Web3.to_checksum_address(pool_address), abi=AAVE_POOL_ABI
    )
    try:
        data = pool.functions.getUserAccountData(safe).call()
        # Base-currency values are 8-decimal in Aave v3.
        collateral_base = data[0] / 1e8
        debt_base = data[1] / 1e8
        if collateral_base == 0 and debt_base == 0:
            print("    (no Aave v3 position)")
            return []
        print(f"    total collateral ~ ${collateral_base:,.2f} | "
              f"total debt ~ ${debt_base:,.2f}")
    except Exception as exception:
        print(f"    error reading getUserAccountData: {exception}")
        return []

    found = []
    try:
        reserves = pool.functions.getReservesList().call()
    except Exception as exception:
        print(f"    error reading getReservesList: {exception}")
        return []

    errored = []
    total_reserves = len(reserves)
    for index, reserve in enumerate(reserves, 1):
        # 67 reserves on Ethereum, and nothing prints for the ~66 holding a
        # zero balance -- this bar is the difference between "working" and
        # "apparently hung".
        progress("Aave v3", index, total_reserves)
        try:
            atoken_address = with_retry(
                lambda: pool.functions.getReserveAToken(reserve).call())
            atoken = w3.eth.contract(address=atoken_address, abi=ERC20_ABI)
            balance = with_retry(lambda: atoken.functions.balanceOf(safe).call())
            if balance > 0:
                decimals = with_retry(lambda: atoken.functions.decimals().call())
                symbol = with_retry(lambda: atoken.functions.symbol().call())
                progress_done()
                print(f"    HOLDS  {symbol:<14} {balance / (10 ** decimals):>18,.6f}"
                      f"   (reserve {reserve})")
                found.append({"reserve": reserve, "atoken": atoken_address, "symbol": symbol})
                if toxicity:
                    # Aave V3: the aToken address IS the poolId, not the
                    # reserve/underlying and not the Pool contract.
                    print_pool_toxicity(atoken_address, chain_key, toxicity, "      ")
        except Exception:
            # NOT the same as "not held": a public RPC's rate limit (429) lands
            # here too. Collecting these separately, rather than silently
            # treating them like a zero balance, is what catches the case
            # where getUserAccountData above reports real collateral but the
            # per-reserve loop below found no matching aToken -- that
            # contradiction means a reserve was skipped, not that it's empty.
            errored.append(reserve)
    progress_done()
    if not found:
        print("    (no aToken balances found among the reserves that could be checked)")
    if errored:
        print(f"    NOTE: {len(errored)}/{len(reserves)} reserve(s) could not be checked "
              f"(RPC error or rate limit) -- re-run to confirm nothing was missed:")
        for reserve in errored:
            print(f"      {reserve}")
    return found


def main():
    safes = [arg for arg in sys.argv[1:] if not arg.startswith("--")] or SAFES_TO_CHECK
    if not safes:
        print(__doc__)
        print("No Safe addresses given. Either pass them as arguments:")
        print("    python3 discover_positions.py 0xSafe1 0xSafe2")
        print("or populate SAFES_TO_CHECK in this file.")
        return

    toxicity = resolve_toxicity()

    # Morpho vault balances are only checked on Ethereum -- MORPHO_VAULT_UNIVERSE
    # has no Base entries yet. Aave v3 is checked on every chain in CHAINS.
    in_scope = {}
    for chain_key, chain_config in CHAINS.items():
        # A timeout is REQUIRED here: public RPCs (mainnet.base.org especially)
        # rate-limit (HTTP 429) under the burst of per-reserve calls check_aave
        # makes, and web3.py's default retry middleware backs off on each 429
        # with no overall time limit -- without this, a rate-limited run doesn't
        # error, it just goes quiet for a very long time. Set <CHAIN>_RPC_URL
        # in config.env to use a paid endpoint and avoid the limits entirely.
        rpc_url = chain_rpc_url(chain_key)
        # Never print rpc_url itself -- a paid endpoint carries its API key in
        # the path, and this output gets screen-shared.
        rpc_label = describe_rpc_url(chain_key)
        w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            print(f"Could not connect to {rpc_label} ({chain_key}). Skipping.")
            continue
        print(f"\n{'#' * 78}\n{chain_key.upper()} -- connected at block "
              f"{w3.eth.block_number}  via {rpc_label}\n{'#' * 78}")

        for raw_safe in safes:
            safe = Web3.to_checksum_address(raw_safe)
            print(f"\n{'=' * 78}\nSafe {safe}\n{'=' * 78}")
            if chain_key == "ethereum":
                for vault in check_morpho_vaults(w3, safe, chain_key, toxicity):
                    in_scope[vault["address"]] = vault
            check_aave(w3, safe, chain_config["aave_pool"], chain_key, toxicity)

    print(f"\n{'=' * 78}")
    if in_scope:
        print("Paste into MORPHO_VAULTS_IN_SCOPE in shared/common.py:\n")
        print("MORPHO_VAULTS_IN_SCOPE = [")
        for vault in in_scope.values():
            print(f'    {{"address": "{vault["address"]}", "symbol": "{vault["symbol"]}"}},')
        print("]")
    else:
        # Say what was actually checked, not "nothing found". Aave reserves
        # are enumerated live from the Pool, but Morpho vaults come from a
        # hardcoded list -- so a vault outside it is invisible here, and
        # phrasing this as a clean result would be a false negative.
        print(f"Morpho vaults: none held, of the {len(MORPHO_VAULT_UNIVERSE)} "
              f"known vaults checked (Ethereum only).")
        print("This is NOT proof the Safe holds no Morpho vaults: unlike Aave")
        print("reserves, which are enumerated live from the Pool, vaults are")
        print("matched against a fixed list (MORPHO_VAULT_UNIVERSE in")
        print("shared/common.py). A vault outside that list cannot be seen here.")
        print("Add its address there to include it (verify on-chain first).")


if __name__ == "__main__":
    main()
