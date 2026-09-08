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
    has no Base entries yet). Nothing is written anywhere. This talks to each
    chain's public RPC, not to Hypernative, so it needs no Hypernative
    credentials and consumes no quota.

USAGE
    1. Put the Safe addresses you want to check in SAFES_TO_CHECK below (or
       pass them as command-line arguments).
    2. python3 discover_positions.py [0xSafe1 0xSafe2 ...]
    3. Copy the vaults it reports into MORPHO_VAULTS_IN_SCOPE in shared/common.py.
"""

import sys

from web3 import Web3

from shared.common import CHAINS, MORPHO_VAULT_UNIVERSE

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


def check_morpho_vaults(w3, safe):
    """Report every verified Morpho vault where this Safe holds shares."""
    print("\n  Morpho vaults:")
    found = []
    for vault in MORPHO_VAULT_UNIVERSE:
        try:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(vault["address"]), abi=VAULT_ABI
            )
            shares = contract.functions.balanceOf(safe).call()
            if shares > 0:
                vault_decimals = contract.functions.decimals().call()
                assets = contract.functions.convertToAssets(shares).call()

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
                print(f"    HOLDS  {symbol:<12} {version:<5} "
                      f"{shares_human:>18,.6f} shares  ~ "
                      f"{assets_human:>16,.2f} {asset_symbol}")
                found.append({"address": vault["address"], "symbol": symbol})
        except Exception as exception:
            label = vault.get("symbol", vault.get("address", "?"))
            print(f"    error  {label:<12} {exception}")
    if not found:
        print("    (no balances in the verified vault universe)")
    return found


def check_aave(w3, safe, pool_address):
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
    for reserve in reserves:
        try:
            atoken_address = pool.functions.getReserveAToken(reserve).call()
            atoken = w3.eth.contract(address=atoken_address, abi=ERC20_ABI)
            balance = atoken.functions.balanceOf(safe).call()
            if balance > 0:
                decimals = atoken.functions.decimals().call()
                symbol = atoken.functions.symbol().call()
                print(f"    HOLDS  {symbol:<14} {balance / (10 ** decimals):>18,.6f}"
                      f"   (reserve {reserve})")
                found.append({"reserve": reserve, "atoken": atoken_address, "symbol": symbol})
        except Exception:
            # NOT the same as "not held": a public RPC's rate limit (429) lands
            # here too. Collecting these separately, rather than silently
            # treating them like a zero balance, is what catches the case
            # where getUserAccountData above reports real collateral but the
            # per-reserve loop below found no matching aToken -- that
            # contradiction means a reserve was skipped, not that it's empty.
            errored.append(reserve)
    if not found:
        print("    (no aToken balances found among the reserves that could be checked)")
    if errored:
        print(f"    NOTE: {len(errored)}/{len(reserves)} reserve(s) could not be checked "
              f"(RPC error or rate limit) -- re-run to confirm nothing was missed:")
        for reserve in errored:
            print(f"      {reserve}")
    return found


def main():
    safes = sys.argv[1:] if len(sys.argv) > 1 else SAFES_TO_CHECK
    if not safes:
        print(__doc__)
        print("No Safe addresses given. Either pass them as arguments:")
        print("    python3 discover_positions.py 0xSafe1 0xSafe2")
        print("or populate SAFES_TO_CHECK in this file.")
        return

    # Morpho vault balances are only checked on Ethereum -- MORPHO_VAULT_UNIVERSE
    # has no Base entries yet. Aave v3 is checked on every chain in CHAINS.
    in_scope = {}
    for chain_key, chain_config in CHAINS.items():
        # A timeout is REQUIRED here: public RPCs (mainnet.base.org especially)
        # rate-limit (HTTP 429) under the burst of per-reserve calls check_aave
        # makes, and web3.py's default retry middleware backs off on each 429
        # with no overall time limit -- without this, a rate-limited run doesn't
        # error, it just goes quiet for a very long time. If it's still slow
        # with your own traffic, point RPC in CHAINS (shared/common.py) at a
        # dedicated endpoint instead of the public one.
        w3 = Web3(Web3.HTTPProvider(chain_config["rpc"], request_kwargs={"timeout": 15}))
        if not w3.is_connected():
            print(f"Could not connect to {chain_config['rpc']} ({chain_key}). Skipping.")
            continue
        print(f"\n{'#' * 78}\n{chain_key.upper()} -- connected at block {w3.eth.block_number}\n{'#' * 78}")

        for raw_safe in safes:
            safe = Web3.to_checksum_address(raw_safe)
            print(f"\n{'=' * 78}\nSafe {safe}\n{'=' * 78}")
            if chain_key == "ethereum":
                for vault in check_morpho_vaults(w3, safe):
                    in_scope[vault["address"]] = vault
            check_aave(w3, safe, chain_config["aave_pool"])

    print(f"\n{'=' * 78}")
    if in_scope:
        print("Paste into MORPHO_VAULTS_IN_SCOPE in shared/common.py:\n")
        print("MORPHO_VAULTS_IN_SCOPE = [")
        for vault in in_scope.values():
            print(f'    {{"address": "{vault["address"]}", "symbol": "{vault["symbol"]}"}},')
        print("]")
    else:
        print("No Morpho vault positions found across the verified vault universe (Ethereum only).")
        print("If you use a vault not in MORPHO_VAULT_UNIVERSE, add it to")
        print("shared/common.py first (verify the address on-chain before trusting it).")


if __name__ == "__main__":
    main()
