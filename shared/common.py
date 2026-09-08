"""
Shared configuration for the "where did our deposit land?" custom agents.

WHAT THESE AGENTS ANSWER
------------------------
A common risk/compliance question: "after we deposit, is there a way to see
where the funds actually land and are held?" Each agent in this directory
fires on a single supply/deposit made *by a monitored Safe* and emits one
plain-English audit line:

    protocol · destination pool/market (+ market id) · asset + decimal-adjusted
    amount · receipt token now held by the Safe + its balance · initiating Safe ·
    tx hash

LICENSING
---------
These are Custom Agents under "Hypernative Platform / Onchain Monitoring &
Automated Response". Check your own plan's limits before deploying many of
them -- typical constraints are pools/contracts, custom agents, chains, and
automated actions.
Nothing here touches the Address Screener / Illicit Funds Tracing product;
confirm separately whether your plan includes it.

!! SANDBOX WARNING !!
---------------------
Constants in this module are for *script-level* use only (building the agent
definition). They are NOT visible inside a PythonProcessingVariable: that code
runs standalone in a RestrictedPython sandbox on the platform with only
`extracted_variables` and the platform helpers in scope. Closing over a
module-level global works in a local `agent.run()` and then fails in production
with `NameError`. Every sandbox function in this directory therefore re-declares
the constants it needs inside its own body. The duplication is deliberate.
"""

import os
import re

from invariantive.common.consts import Chain


def _load_config_env():
    """Load config.env (repo root) into os.environ, if present.

    Dependency-free by design: this repo's only third-party need is web3,
    and adding python-dotenv purely to parse KEY=VALUE lines isn't worth a
    new requirement. Real environment variables always win over the file.
    """
    path = os.path.join(os.path.dirname(__file__), "..", "config.env")
    if not os.path.isfile(path):
        return
    with open(path) as config_file:
        for line in config_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and key not in os.environ:
                os.environ[key] = value.strip()


_load_config_env()

# --------------------------------------------------------------------------
# Chain. Your plan may allow several chains; this tool is scoped to Ethereum
# mainnet only. To add a chain, change CHAIN and supply that chain's
# own protocol addresses -- Aave/Morpho deploy at DIFFERENT addresses per chain.
# Verified: the enum member is lowercase `Chain.ethereum`.
# --------------------------------------------------------------------------
CHAIN = Chain.ethereum

# --------------------------------------------------------------------------
# PLACEHOLDER -- must be replaced before deployment.
#
# A Hypernative address List holding every Safe you want to monitor. Using a
# List (rather than hard-coded addresses) is what lets you add a new Safe in
# the UI without anyone editing or redeploying this code.
#
# Create at:  Settings > Lists > Add List, CSV columns `chain,address,note`.
# The `note` column becomes the human label in the alert (e.g. "Treasury Safe 2"),
# so fill it in -- it is what makes the audit line readable to whoever reviews it.
# --------------------------------------------------------------------------
SAFE_LIST_UUID = "SAFE_LIST_UUID"

# --------------------------------------------------------------------------
# PLACEHOLDER -- must be replaced before deployment.
# Notification channel id(s). Get them from:
#   Hypernative app > Actions > Notification Channels > open channel > id in URL
#   or: GET https://api.hypernative.xyz/notification-channels
#       headers: x-client-id, x-client-secret
# Supported integrations: Slack, Email, API, Telegram, Discord.
# --------------------------------------------------------------------------
NOTIFICATION_CHANNEL_IDS = []  # e.g. [{"id": 1337}]

# Record-keeping, not a threat alert -> Info.
# NOTE: AlertConfig has NO severity parameter (verified against the installed
# SDK's dataclass fields). Severity is a deploy() argument. save_config() bakes
# an inner severity of "medium" into the rule JSON which the server normalises
# on create -- cosmetic only, and not settable from Python.
SEVERITY = "Info"

# ==========================================================================
# AAVE V3 -- Ethereum mainnet
# ==========================================================================
AAVE_V3_POOL = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# event Supply(
#     address indexed reserve,      -> emitted_arg_0
#     address user,                 -> emitted_arg_1   (the caller)
#     address indexed onBehalfOf,   -> emitted_arg_2   (receives the aTokens)
#     uint256 amount,               -> emitted_arg_3
#     uint16 indexed referralCode   -> emitted_arg_4
# )
#
# The index mapping above is NOT an assumption -- it was resolved empirically by
# running probe_aave_args.py against AAVE_TX_USER_NE_ONBEHALF, where `user` is a
# gateway contract and therefore differs from `onBehalfOf`:
#     arg0 = 0xc02aaa39...  (WETH)                 = reserve
#     arg1 = 0xd01607c3...  (WrappedTokenGatewayV3) = user
#     arg2 = 0x029a0f3a...                          = onBehalfOf
#     arg3 = 81000000000000000                      = amount
#     arg4 = 0                                      = referralCode
# This matters because Aave declares its indexed params out of order
# (reserve, onBehalfOf, referralCode are indexed; user, amount are not), so the
# on-the-wire topics/data order differs from the declaration order. The probe
# proves Hypernative numbers emitted_arg_N by DECLARATION order.
AAVE_ARG_RESERVE = "emitted_arg_0"
AAVE_ARG_USER = "emitted_arg_1"
AAVE_ARG_ONBEHALFOF = "emitted_arg_2"
AAVE_ARG_AMOUNT = "emitted_arg_3"

# There is no `Deposit` event on the Aave v3 Pool. `deposit()` is a deprecated
# alias that routes to the same logic and emits `Supply`. (`Deposit` is Aave v2.)
AAVE_EVENT = "Supply"

# Verified fixture transactions (real Ethereum mainnet, all status: success).
AAVE_TX_USDT_SIMPLE = "0x24932d894c922bc8a42a1de246c30132a1a5da4699091b72a44a308b60b89e77"
AAVE_TX_USER_NE_ONBEHALF = "0xe3b4c43a6a8ad881f8abade44cce20a31adb91ce51109492f88be332ab01e7dc"
AAVE_TX_USDC_SMALL = "0xfb194cca437c5fb452b60666c1252b0283127808efc24e0f654087413816b7f2"

# ==========================================================================
# MORPHO BLUE -- Ethereum mainnet
# ==========================================================================
# Verified on Etherscan: "Exact Match", contract name `Morpho`, full ABI
# published, NOT a proxy. EIP-55 checksum of the lowercase form in the brief.
MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

# event Supply(Id indexed id, address indexed caller, address indexed onBehalf,
#              uint256 assets, uint256 shares)      -- `Id` is `type Id is bytes32`
# Here the 3 indexed params ARE declared first, so declaration order and wire
# order coincide and the mapping is unambiguous either way.
MORPHO_ARG_MARKET_ID = "emitted_arg_0"
MORPHO_ARG_CALLER = "emitted_arg_1"
MORPHO_ARG_ONBEHALF = "emitted_arg_2"
MORPHO_ARG_ASSETS = "emitted_arg_3"
MORPHO_ARG_SHARES = "emitted_arg_4"

# NOTE: `SupplyCollateral` is a SEPARATE event that moves the market's
# collateralToken (not the loanToken) and mints no shares. If collateral
# deposits need tracking too, add a separate agent rather than bending this one.

MORPHO_TX_USDC_LARGE = "0x56a7311655f0d7418f27e949b076066ece08238d08840d62d2b091e244547496"
MORPHO_TX_WETH_18DP = "0x90e199d61e5d748edf92c9bf801c78d5f9f51a94dd42b85dc03b8c8e954a46f2"
MORPHO_TX_CALLER_NE_ONBEHALF = "0x6e6648a56fda0fde349f4ce9d7f98f420b0c3a6a29c316e86acc8fdd8487e895"

# ==========================================================================
# MORPHO VAULTS (MetaMorpho V1 / V1.1 / Vault V2) -- Ethereum mainnet
# ==========================================================================
# event Deposit(address indexed sender, address indexed owner,
#               uint256 assets, uint256 shares)
# ONE definition covers MetaMorpho V1, MetaMorphoV1_1 AND Vault V2: V1 inherits
# it from OpenZeppelin IERC4626, V2 re-declares it with the 2nd param renamed
# `owner` -> `onBehalf` but identical types, indexed flags and order, hence the
# same topic0. Verified empirically: V2 logs decode with the V1 shape.
VAULT_EVENT = "Deposit"
VAULT_ARG_SENDER = "emitted_arg_0"
VAULT_ARG_OWNER = "emitted_arg_1"
VAULT_ARG_ASSETS = "emitted_arg_2"
VAULT_ARG_SHARES = "emitted_arg_3"

# PLACEHOLDER -- confirm which vaults you actually use.
# Run discover_positions.py to find out (it reads balanceOf across the verified
# vault universe for each Safe). One agent is created per vault, because an
# EventTrigger takes a single concrete contract_address.
#
# The list below is the *verified live* vault universe (on-chain reads), provided
# as a menu to confirm against -- NOT an assumption about your holdings.
# `vault_decimals` is recorded here only to document the offset trap; the agent
# reads it on-chain rather than trusting this table.
MORPHO_VAULT_UNIVERSE = [
    {"address": "0xbEef047a543E45807105E51A8BBEFCc5950fcfBa", "symbol": "steakUSDT",  "asset": "USDT", "asset_dec": 6,  "vault_dec": 18, "version": "V1"},
    {"address": "0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB", "symbol": "steakUSDC",  "asset": "USDC", "asset_dec": 6,  "vault_dec": 18, "version": "V1"},
    {"address": "0xBEEf050ecd6a16c4e7bfFbB52Ebba7846C4b8cD4", "symbol": "steakETH",   "asset": "WETH", "asset_dec": 18, "vault_dec": 18, "version": "V1"},
    {"address": "0xdd0f28e19C1780eb6396170735D45153D261490d", "symbol": "gtUSDC",     "asset": "USDC", "asset_dec": 6,  "vault_dec": 18, "version": "V1"},
    {"address": "0xA0804346780b4c2e3bE118ac957D1DB82F9d7484", "symbol": "bbqUSDT",    "asset": "USDT", "asset_dec": 6,  "vault_dec": 18, "version": "V1.1"},
    {"address": "0xBEeFFF209270748ddd194831b3fa287a5386f5bC", "symbol": "bbqUSDC",    "asset": "USDC", "asset_dec": 6,  "vault_dec": 18, "version": "V1"},
    {"address": "0x04422053aDDbc9bB2759b248B574e3FCA76Bc145", "symbol": "kUSDC",      "asset": "USDC", "asset_dec": 6,  "vault_dec": 18, "version": "V2"},
    {"address": "0x093272C07700d3cA5301C3Bf9B3A392624179E2F", "symbol": "hyperUSDCa", "asset": "USDC", "asset_dec": 6,  "vault_dec": 18, "version": "V2"},
]

# Which vaults to actually build agents for. Populate from discover_positions.py
# output, once confirmed. Empty = build for nothing (fail loudly).
MORPHO_VAULTS_IN_SCOPE = []

# Verified fixture transactions.
VAULT_TX_USDT_SIMPLE = "0xf297d8325bf7de54d03416279cdc401b3f57ab82a4dba212158c8d42367aceef"   # steakUSDT, 480,234.499741 USDT
VAULT_TX_WETH_OFFSET0 = "0x30695d29e3656f340b9028c1d072e863aa2d4be174390cbb592e610d212ceff0"  # steakETH, DECIMALS_OFFSET=0 control
VAULT_TX_SENDER_NE_OWNER = "0xdd43770f094c546acff3e56a23b65b47dc2c83d2d416e8e3b7706df6aa1e8fc2"  # steakUSDC via bundler
VAULT_TX_V2 = "0xb9e554dc2a9569bdd4d6b8a1f8296caf07b52bfed5960fcfe541895f219852b7"             # kUSDC (Vault V2)

# Morpho's Bundler3 adapter. When a deposit goes through the Morpho UI this is
# the `sender`, and the Safe is the `owner` -- which is exactly why every agent
# here filters on the OWNER/onBehalfOf field and never on `sender`/`tx_from`.
MORPHO_GENERAL_ADAPTER = "0x4a6c312ec70e8747a587ee860a0353cd42be0ae0"


def is_quiet_mode():
    """True if the script was invoked with --quiet or -q.

    Shared by every agent's __main__ block so the flag behaves identically
    across all three files. Demo-friendly: `python3 agent_*.py --quiet` prints
    only the audit lines, nothing else, suitable for screen-sharing with a
    customer without a debug-variable dump.
    """
    import sys
    return "--quiet" in sys.argv or "-q" in sys.argv


# --------------------------------------------------------------------------
# Terminal presentation for --quiet. Local display only.
#
# These helpers deliberately live here and NOT in the agents'
# build_audit_line() functions. That string is the alert text that actually
# gets sent to Slack and email, so putting ANSI escape codes into it would
# corrupt the real notification. Wrapping and colour are applied at print time
# and add nothing but newlines and escape codes: the visible characters stay
# byte-for-byte the deployed alert.
# --------------------------------------------------------------------------

# Raw ANSI, no dependency. `rich` and `colorama` are not installed, and adding
# a pip dependency purely for cosmetics is a bad trade for a script that may be
# run on a colleague's or the customer's laptop.
ANSI_RESET = "\033[0m"
ANSI_BOLD_CYAN = "\033[1;36m"
ANSI_GREEN = "\033[0;92m"
ANSI_DIM = "\033[2;37m"
ANSI_YELLOW = "\033[0;33m"

# ONE combined pattern, applied in a SINGLE pass.
#
# This must not be split into sequential re.sub() calls. Doing so re-scans text
# that a previous substitution already coloured, and the number pattern then
# matches the DIGITS INSIDE an ANSI escape code (the "2" and "37" of
# \033[2;37m), injecting colour codes into the middle of a colour code and
# corrupting the sequence. A single pass never revisits its own output.
#
# Alternation order matters: `hex` must precede `num`, or the leading "0" of an
# address would be claimed as a number first.
RE_HIGHLIGHT = re.compile(
    r"(?P<dep>^Deposit:)"
    r"|(?P<hex>0x[0-9a-fA-F]{6,})"
    # Amounts, balances, block numbers. The lookarounds are load-bearing: they
    # stop "Aave v3" and the "10DEC2026" inside a market name like
    # PT-reUSD-10DEC2026 from being treated as numbers.
    r"|(?P<num>(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w]))"
)

# Which colour each named group gets.
HIGHLIGHT_COLORS = {
    "dep": ANSI_BOLD_CYAN,   # the line's subject
    "hex": ANSI_DIM,         # present, but should not out-shout the amount
    "num": ANSI_GREEN,       # amounts and balances, the thing you look at
}


def is_terminal():
    """True when stdout is an interactive terminal.

    Gates WRAPPING. When output is redirected (`--quiet | grep`, or into a
    file) we emit one line per finding so the grep recipes in README.md and
    any downstream tooling keep working.
    """
    import sys
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def supports_color():
    """True when it is safe to emit ANSI escape codes.

    Gates COLOUR only, deliberately separate from is_terminal(). Someone who
    sets NO_COLOR (https://no-color.org) as a standing preference still wants
    readable wrapped output, so NO_COLOR must not silently turn the wall of
    text back on.
    """
    import os
    if os.environ.get("NO_COLOR"):
        return False
    return is_terminal()


def highlight(line):
    """Colourise one ALREADY-WRAPPED line, in a single regex pass.

    Must run after wrapping, never before: textwrap counts ANSI escape bytes as
    visible width, so colourising first produces ragged, wrongly-wrapped output.
    """
    def paint(match):
        name = match.lastgroup
        return f"{HIGHLIGHT_COLORS[name]}{match.group(name)}{ANSI_RESET}"

    return RE_HIGHLIGHT.sub(paint, line)


def format_audit_line(description, width=None, color=None, wrap=None):
    """Wrap and colourise one audit line for terminal display.

    Wrapping and colour are independent: `wrap` follows is_terminal() so piped
    output stays one line per finding, while `color` additionally honours
    NO_COLOR. Both add nothing but newlines and escape codes, so the visible
    characters remain byte-for-byte the deployed alert.
    """
    import shutil
    import textwrap

    if color is None:
        color = supports_color()
    if wrap is None:
        wrap = is_terminal()

    # Redirected: one line per finding, no escape codes.
    if not wrap:
        return description

    if width is None:
        # Cap the width: full-bleed text on a wide window is harder to read
        # than a comfortable measure, and this is meant for a projector.
        width = min(shutil.get_terminal_size((100, 20)).columns, 100)

    # break_long_words=False keeps a 42-char address or 66-char hash on one
    # line. A split token would also defeat the regex in highlight().
    # break_on_hyphens=False keeps names like PT-reUSD-10DEC2026 intact.
    lines = textwrap.wrap(
        description,
        width=max(width - 2, 40),
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )

    # The NOTE clause is handled across lines rather than per line, because it
    # usually wraps. Matching it with a line-anchored regex would colour only
    # its first line and drop the highlight on the continuation, which is worse
    # than not highlighting it at all. Everything from "NOTE:" to the end of
    # the message is one yellow block.
    if not color:
        return "\n".join(lines)

    painted = []
    in_note = False
    for line in lines:
        if in_note:
            painted.append(f"{ANSI_YELLOW}{line}{ANSI_RESET}")
            continue
        cut = line.find("NOTE:")
        if cut == -1:
            painted.append(highlight(line))
        else:
            in_note = True
            painted.append(
                highlight(line[:cut]) + f"{ANSI_YELLOW}{line[cut:]}{ANSI_RESET}"
            )
    return "\n".join(painted)


# --------------------------------------------------------------------------
# Terminal presentation for the default (non-quiet) view. Local display only,
# same rationale as the colour/wrap helpers above: this groups and labels the
# SAME extracted_variables dict the agent produced, it never changes a value.
#
# The three agents name a handful of roles differently (Aave's `caller_user`,
# Morpho Blue's `caller`, the vault's `sender` are all "who initiated this"),
# so this table is keyed by the union of all three agents' variable names
# rather than assuming one schema.
# --------------------------------------------------------------------------
VARIABLE_SECTIONS = {
    # Who was involved
    "safe_address": ("Who", "Safe (holds the resulting position)"),
    "caller_user": ("Who", "Initiated by"),
    "caller": ("Who", "Initiated by"),
    "sender": ("Who", "Initiated by"),
    "executed_by": ("Who", "Paid gas for the transaction"),

    # What was deposited
    "amount_raw": ("What was deposited", "Amount deposited (raw base units)"),
    "assets_raw": ("What was deposited", "Amount deposited (raw base units)"),
    "asset_symbol": ("What was deposited", "Asset"),
    "asset_decimals": ("What was deposited", "Asset decimals"),
    "loan_symbol": ("What was deposited", "Asset"),
    "loan_decimals": ("What was deposited", "Asset decimals"),
    "reserve": ("What was deposited", "Asset contract"),
    "loan_token": ("What was deposited", "Asset contract"),
    "underlying": ("What was deposited", "Asset contract"),
    "shares_minted": ("What was deposited", "Shares minted by this deposit"),

    # Where it landed -- protocol-specific receipt token / position
    "atoken": ("Where it landed", "aToken contract"),
    "atoken_symbol": ("Where it landed", "aToken"),
    "atoken_balance": ("Where it landed", "Safe's new balance"),
    "vault_address": ("Where it landed", "Vault contract"),
    "vault_name": ("Where it landed", "Vault name"),
    "vault_symbol": ("Where it landed", "Vault share token"),
    "vault_decimals": ("Where it landed", "Vault share decimals"),
    "shares_after_raw": ("Where it landed", "Safe's new share balance (raw, vault decimals)"),
    "assets_after_raw": ("Where it landed", "Currently redeemable for (raw, asset decimals)"),
    "market_id": ("Where it landed", "Market id"),
    "collateral_token": ("Where it landed", "Collateral contract"),
    "collateral_symbol": ("Where it landed", "Collateral asset"),
    "lltv": ("Where it landed", "LLTV (raw, 18-decimal fraction)"),
    "supply_shares_after": ("Where it landed", "Safe's new supply shares"),
    "total_supply_assets": ("Where it landed", "Market total supplied assets"),
    "total_supply_shares": ("Where it landed", "Market total supply shares"),

    # Transaction identity
    "tx_hash": ("Transaction", "Tx hash"),
    "block_number": ("Transaction", "Block"),
    "block_timestamp": ("Transaction", "Time"),
}

# Fixed print order. A section with nothing in it for a given finding is
# skipped; "Other" catches any variable a future agent adds that this table
# doesn't know about yet, rather than silently dropping it.
VARIABLE_SECTION_ORDER = ["Who", "What was deposited", "Where it landed", "Transaction", "Other"]

# Whichever of these is present names "who initiated this" for the given
# agent -- see the module-level comment on VARIABLE_SECTIONS.
INITIATOR_KEYS = ("caller_user", "caller", "sender")


def print_variables(variables):
    """Print one finding's extracted_variables, grouped and labelled.

    Two keys are dropped rather than shown raw: `audit_line` (an exact copy
    of the ALERT text already printed above this) and `formatted` (a second,
    nested copy of audit_line/amount/amount_text/balance_text -- every agent's
    PythonProcessingVariable produces both). The one part of `formatted` that
    is not a duplicate, `initiator_mismatch`, is pulled out first and
    rendered as a plain-English sentence instead of a bool buried in a repr.
    """
    remaining = dict(variables)
    formatted = remaining.pop("formatted", None) or {}
    remaining.pop("audit_line", None)

    sections = {}
    for key, value in remaining.items():
        section, label = VARIABLE_SECTIONS.get(key, ("Other", key))
        sections.setdefault(section, []).append((label, value))

    mismatch = formatted.get("initiator_mismatch")
    if mismatch is not None:
        sentence = (
            "Initiated by a different address than the Safe that ends up "
            "holding the position."
            if mismatch else
            "Initiated by the Safe itself."
        )
        sections.setdefault("Who", []).append((None, sentence))

    for section in VARIABLE_SECTION_ORDER:
        lines = sections.get(section)
        if not lines:
            continue
        lines.sort(key=lambda pair: (pair[0] is None, (pair[0] or "").lower()))
        print(f"  {section}:")
        for label, value in lines:
            if label is None:
                print(f"    {value}")
            else:
                print(f"    {label}: {value}")


def print_findings(result, title="", quiet=False):
    """Print an agent.run() result.

    quiet=False (default): the full local-testing view -- a banner with the
    finding count, the audit line, and every extracted variable grouped into
    plain-English sections (see print_variables). Useful while building or
    debugging an agent.

    quiet=True: just the audit line(s), wrapped to the terminal, colourised,
    and separated by a blank line. No banner, no variable dump, no "0 findings"
    noise. Meant to be screen-shared with a customer. The visible text is
    identical to the alert they will receive.

    Local helper for the __main__ test blocks only -- never referenced from
    inside a PythonProcessingVariable.
    """
    findings = result.get("findings", []) or []

    if quiet:
        for finding in findings:
            print(format_audit_line(str(finding.get("description"))))
            print()
        return

    print(f"\n{'=' * 78}")
    print(f"{title}   ->  {len(findings)} finding(s)")
    print("=" * 78)
    if not findings:
        print("  (no findings -- if this is unexpected, check that the Safe filter's")
        print("   List contains the address in the fixture transaction)")
    for n, finding in enumerate(findings, 1):
        print(f"\n--- finding {n} ---")
        print("ALERT: " + str(finding.get("description")))
        variables = finding.get("extracted_variables", {}) or {}
        print_variables(variables)
