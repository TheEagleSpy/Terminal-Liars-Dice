import random
import time
import os
import json
from math import comb
from collections import deque

# === ANSI COLOURS ===
GREEN = "\033[92m"
RESET = "\033[0m"

AI_MEMORY_FILE = "ai_memory.json"

# --- Utilities ---
def clear_cmd():
    os.system("cls" if os.name == "nt" else "clear")

def Print(text, delay=0.03, newline=True):
    for c in str(text):
        print(c, end="", flush=True)
        time.sleep(delay)
    if newline:
        print()

def press_to_continue(msg="\nPress Enter to continue: "):
    try:
        input(msg)
    except EOFError:
        pass

def render_turn_order(order, active_set, current):
    """Single-line turn order, removes eliminated, highlights current in green."""
    seq = []
    seen = set()
    for name in list(order):
        if name in seen:
            continue
        seen.add(name)
        if name not in active_set:
            continue
        if name == current:
            seq.append(f"{GREEN}{name}{RESET}")
        else:
            seq.append(name)
    return " -> ".join(seq)

# --- Persistent AI memory ---
# We expand the stat schema to support emergent behavior.
BASE_STATS = {
    "bluffs_caught": 0,      # times this player was caught bluffing
    "defended_success": 0,   # times this player’s bid was called and proven true
    "bluffs_made": 0,        # bids that ended up being bluffs
    "bluff_success": 0,      # bluff called and survived (rare in pure Liar’s Dice; included for completeness)
    "truths_made": 0,        # bids that ended up being true
    "truth_success": 0       # truth called and survived
}

def _merge_dicts(a, b, scale=1.0):
    out = {}
    for k in BASE_STATS:
        out[k] = a.get(k, 0) + scale * b.get(k, 0)
    return out

def load_ai_memory(players):
    data = {}
    if os.path.exists(AI_MEMORY_FILE):
        try:
            with open(AI_MEMORY_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                # migrate older schema if needed
                for p in players:
                    d = raw.get(p, {})
                    entry = {}
                    for k in BASE_STATS:
                        entry[k] = int(d.get(k, 0))
                    data[p] = entry
        except Exception:
            data = {}
    # ensure all players exist
    for p in players:
        data.setdefault(p, {k: 0 for k in BASE_STATS})
    return data

def save_ai_memory(memory):
    try:
        with open(AI_MEMORY_FILE, "w", encoding="utf-8") as f:
            json.dump(memory, f, indent=2)
    except Exception:
        pass

def merge_match_into_global(global_mem, match_mem):
    for name, stats in match_mem.items():
        gm = global_mem.setdefault(name, {k: 0 for k in BASE_STATS})
        for k in BASE_STATS:
            gm[k] += stats.get(k, 0)

# --- Probability helper ---
def prob_at_least(n, k, p=1/6):
    if n <= 0:
        return 1.0
    if n > k:
        return 0.0
    total = 0.0
    for i in range(n, k + 1):
        total += comb(k, i) * (p ** i) * ((1 - p) ** (k - i))
    return total

# --- Dialogue templates ---
table_talk = {
    "pre_bid": [
        "[{name}] Hmm... let’s start with this...",
        "[{name}] Starting strong with {next_qty} {face}'s.",
        "[{name}] Don’t get too confident {target}.",
        "[{name}] Haha, good luck guys.",
        "[{name}] Let's start off slowly.",
        "[{name}] Well this is interesting...",
        "[{name}] I've already won this game.",
        "[{name}] Let’s get this started.",
        "[{name}] Careful, {target}. I’m watching you.",
        "[{name}] I can feel the tension in the air."
    ],
    "raise": [
        "[{name}] Let's go with {new_qty} {new_face}'s.",
        "[{name}] Aww scared are we?. {new_qty} {new_face}'s.",
        "[{name}] Haha, Try this, {new_qty} {new_face}'s.",
        "[{name}] Watch and learn... {new_qty} {new_face}'s.",
        "[{name}] Can you top that? {new_qty} {new_face}'s.",
        "[{name}] I raise it to {new_qty} {new_face}'s.",
        "[{name}] {new_qty} {new_face}'s.",
        "[{name}] Let’s see how you guys handle {new_qty} {new_face}'s.",
        "[{name}] This is the last truthful bid. {new_qty} {new_face}'s."
    ],
    "call_bluff": [
        "[{name}] Haha, thats a bluff",
        "[{name}] Bluff! No way that’s true.",
        "[{name}] I don’t buy it.",
        "[{name}] Let’s see what you’re hiding.",
        "[{name}] I’m calling you out!",
        "[{name}] You sure about that one?",
        "[{name}] Not convinced.",
        "[{name}] Let’s check those dice.",
        "[{name}] I think you’re fibbing.",
        "[{name}] Even you know that bid was too high."
    ]
}

# --- Rate helpers for emergent behavior ---
def _safe_rate(num, den):
    return num / max(1, den)

def combined_stats(name, match_memory, global_memory, global_weight=0.5):
    m = match_memory.get(name, {k:0 for k in BASE_STATS})
    g = global_memory.get(name, {k:0 for k in BASE_STATS})
    return _merge_dicts(m, g, scale=global_weight)

def record_bid_outcome(bidder, was_truth, was_success, match_memory):
    s = match_memory[bidder]
    if was_truth:
        s["truths_made"] += 1
        if was_success:
            s["truth_success"] += 1
    else:
        s["bluffs_made"] += 1
        if was_success:
            s["bluff_success"] += 1

def play_liars_dice(player_data, gold_bet, enemy_names, difficulty):
    # Normalize to 8 players including Knight
    while len(enemy_names) < 7:
        enemy_names.append(f"Opponent {len(enemy_names)+1}")

    players = ["Knight"] + enemy_names[:7]
    active_players = {name: [] for name in players}

    # Secret partner pairs, random per match
    shuffled = players[:]
    random.shuffle(shuffled)
    partners = {}
    for i in range(0, len(shuffled), 2):
        a, b = shuffled[i], shuffled[i+1]
        partners[a] = b
        partners[b] = a

    # Per-match memory (fresh)
    match_memory = {name: {k: 0 for k in BASE_STATS} for name in players}
    # Persistent memory across sessions
    global_memory = load_ai_memory(players)

    # Ante
    ante_each = gold_bet // len(players)
    pot = ante_each * len(players)
    leftover = gold_bet - pot
    if leftover > 0:
        player_data["gold"] += leftover

    diff = difficulty.lower()
    confidence_mods = {
        "easy":   (0.28, 0.45),
        "medium": (0.45, 0.65),
        "hard":   (0.65, 0.85),
    }
    low, high = confidence_mods.get(diff, (0.45, 0.65))
    share = pot // 2  # split between final two

    # Opening text, wording change
    Print(f"\nYou sit at a crowded tavern table with {len(players)} players.")
    Print(f"Total pot is {pot} gold coins, each player adds {ante_each} to the pot. Top 2 split 50/50.\n")

    watching = False
    eliminated_order = []

    # Table order
    turn_order = deque(players)
    random.shuffle(turn_order)

    allow_quit = False
    round_start_index = 0

    # Track who made the current highest bid
    current_bid = None           # (qty, face)
    current_bidder = None        # name

    while len(active_players) > 2:
        # Find a valid starter still active
        for _ in range(len(turn_order)):
            starter = turn_order[round_start_index % len(turn_order)]
            if starter in active_players:
                break
            round_start_index += 1

        ordered = list(turn_order)
        start_pos = ordered.index(starter)
        round_order = deque(ordered[start_pos:] + ordered[:start_pos])

        # Prompt to roll, clear, then show turn order line
        press_to_continue("Press Enter to roll dice and begin the round: ")
        clear_cmd()

        active_set = set(active_players.keys())
        Print(render_turn_order(round_order, active_set, starter))

        # Roll dice
        for name in list(active_players.keys()):
            active_players[name] = [random.randint(1, 6) for _ in range(4)]

        partner_name = partners.get("Knight")
        partner_dice = active_players[partner_name] if partner_name in active_players else []

        if "Knight" in active_players:
            Print(f"Your Dice: {active_players['Knight']} | Partner {partner_name if partner_name else '-'}: {partner_dice}")

        current_bid = None
        current_bidder = None
        round_over = False
        Print(f"\n{starter} starts the round.")

        # Round loop
        while not round_over:
            for _ in range(len(round_order)):
                if round_over:
                    break

                if allow_quit and watching:
                    choice = input("[Q] Quit now, or press Enter to keep watching: ").strip().lower()
                    if choice == "q":
                        Print("\nYou leave the table early, no final payouts are calculated.")
                        merge_match_into_global(global_memory, match_memory)
                        save_ai_memory(global_memory)
                        return

                player = round_order[0]
                round_order.rotate(-1)
                if player not in active_players:
                    continue

                total_players_left = len(active_players)
                total_dice = sum(len(d) for d in active_players.values())

                # --- Knight turn (fixed input loop) ---
                if player == "Knight":
                    while True:  # keep asking until a valid action is performed
                        current_bid_text = f"{current_bid[0]} {current_bid[1]}'s" if current_bid else "No bids yet"
                        partner_name = partners.get("Knight")
                        partner_dice = active_players.get(partner_name, [])

                        Print("\n---------------------------")
                        Print(f"Your Dice: {active_players['Knight']} | Partner {partner_name if partner_name else '-'}: {partner_dice}")
                        Print(f"Players Left: {total_players_left} | Total Dice: {total_dice}")
                        Print(f"Current Bid: {current_bid_text}")
                        Print("[1] Up Bid")
                        Print("[2] Call Bluff")
                        action = input("Enter: ").strip()
                        Print("---------------------------")

                        if action not in ("1", "2"):
                            Print("\mInvalid choice, Enter 1 to Up Bid or 2 to Call Bluff.")
                            continue  # do not end Knight’s turn

                        # CALL BLUFF
                        if action == "2":
                            if not current_bid or not current_bidder:
                                Print("\nNo bid to call bluff on.")
                                continue  # still Knight’s turn

                            qty, face = current_bid

                            Print("\n--- ALL DICE REVEALED ---")
                            for name, dice in active_players.items():
                                Print(f"{name}: {dice}")
                            Print("--------------------------\n")

                            actual_count = sum(d.count(face) for d in active_players.values())
                            Print(f"The bid was {qty} {face}'s, there are {actual_count} {face}'s.")

                            bidder = current_bidder
                            was_truth = actual_count >= qty

                            if was_truth:
                                # Knight wrong, bidder defended
                                Print("Knight loses the bluff and is OUT!")
                                if "Knight" in active_players:
                                    del active_players["Knight"]
                                watching = True
                                allow_quit = True
                                eliminated_order.append("Knight")

                                # Stats
                                match_memory[bidder]["defended_success"] += 1
                                record_bid_outcome(bidder, was_truth=True, was_success=True, match_memory=match_memory)
                            else:
                                # Bidder bluffing, bidder out
                                Print(f"{bidder} was bluffing and is OUT!")
                                if bidder in active_players:
                                    del active_players[bidder]
                                eliminated_order.append(bidder)

                                # Stats
                                match_memory[bidder]["bluffs_caught"] += 1
                                record_bid_outcome(bidder, was_truth=False, was_success=False, match_memory=match_memory)

                            round_over = True
                            break  # Knight’s action completed

                        # UP BID
                        else:
                            while True:
                                bet = input("Enter Bid: ").strip().split()
                                if len(bet) != 2 or not all(x.isdigit() for x in bet):
                                    Print("Invalid format, example: 3 4")
                                    continue
                                qty, face = map(int, bet)
                                if not (1 <= face <= 6):
                                    Print("Face must be 1–6.")
                                    continue
                                if qty < 2 and not current_bid:
                                    Print("Minimum opening bid is 2 of a kind.")
                                    continue
                                if current_bid and (qty < current_bid[0] or (qty == current_bid[0] and face <= current_bid[1])):
                                    Print("Bid must be higher than current.")
                                    continue
                                if qty > total_dice:
                                    Print(f"Quantity too high, max is {total_dice}.")
                                    continue
                                current_bid = (qty, face)
                                current_bidder = "Knight"
                                Print(f"Knight bids {qty} dice of {face}'s.")
                                break  # bid accepted
                            break  # Knight’s action completed

                # --- NPC turn ---
                else:
                    if player == "Knight" and watching:
                        Print("You watch the table")
                        continue

                    # NPC pacing
                    if watching or "Knight" in active_players:
                        time.sleep(random.uniform(0.35, 0.75))

                    dice = active_players[player]
                    partner = partners.get(player)
                    partner_dice = active_players.get(partner, [])

                    threshold_third = (total_dice + 2) // 3  # ceil(total_dice/3)

                    # Prepare emergent stats (self + opponent)
                    self_stats = combined_stats(player, match_memory, global_memory, global_weight=0.5)
                    bluff_rate_self = _safe_rate(self_stats["bluffs_made"], self_stats["truths_made"] + self_stats["bluffs_made"])
                    bluff_success_self = _safe_rate(self_stats["bluff_success"], self_stats["bluffs_made"])
                    defend_success_self = _safe_rate(self_stats["defended_success"], self_stats["defended_success"] + self_stats["bluffs_caught"])

                    # Opening bid
                    if not current_bid:
                        face_counts = {f: dice.count(f) for f in range(1, 7)}
                        common_face = max(face_counts, key=face_counts.get)
                        face_guess = common_face if random.random() < 0.7 else random.randint(2, 5)
                        base_qty = dice.count(face_guess)
                        if partner_dice and random.random() < 0.55:
                            base_qty += partner_dice.count(face_guess)

                        # Confidence bump from past success
                        confidence_factor = 1.0 + (bluff_success_self - 0.5) * (0.6 if diff == "hard" else 0.35)
                        qty_guess = int(max(2, min(base_qty + random.choice([0, 1]) * confidence_factor, total_dice)))
                        current_bid = (qty_guess, face_guess)
                        current_bidder = player
                        if watching or "Knight" in active_players:
                            Print(f"\n{random.choice(table_talk['pre_bid']).format(name=player, next_qty=qty_guess, face=face_guess, target='Knight')}")
                            Print(f"{player} opens with {qty_guess} {face_guess}'s.\n")
                        continue

                    # Evaluate call vs raise against current bidder
                    qty, face = current_bid
                    known_count = dice.count(face)
                    if partner_dice and random.random() < (0.5 if diff == "hard" else 0.35):
                        known_count += partner_dice.count(face)

                    unknown_dice = total_dice - len(dice) - len(partner_dice)
                    need = max(0, qty - known_count)
                    p_true = prob_at_least(need, unknown_dice, p=1/6)

                    estimate_factor = random.uniform(low, high)
                    estimated_total = known_count + int(unknown_dice * estimate_factor)
                    estimated_total += random.randint(-1, 1)
                    bluff_margin = qty - estimated_total

                    call_chance = 0.08 + max(0, bluff_margin) * 0.10
                    call_chance += (1.0 - p_true) * (0.75 if diff == "hard" else 0.45)

                    if qty > threshold_third:
                        call_chance += 0.15 * (qty - threshold_third)

                    if qty > 8:
                        call_chance += 0.12 + (qty - 8) * 0.03

                    if diff == "easy":
                        call_chance *= 0.65
                    elif diff == "hard":
                        call_chance *= 1.10

                    # Opponent modeling: the CURRENT bidder we are judging
                    bidder = current_bidder
                    if bidder:
                        opp_stats = combined_stats(bidder, match_memory, global_memory, global_weight=0.5)
                        opp_bluff_rate = _safe_rate(opp_stats["bluffs_made"], opp_stats["truths_made"] + opp_stats["bluffs_made"])
                        opp_bluff_success_rate = _safe_rate(opp_stats["bluff_success"], opp_stats["bluffs_made"])
                        opp_defend_success_rate = _safe_rate(opp_stats["defended_success"], opp_stats["defended_success"] + opp_stats["bluffs_caught"])

                        # If bidder often bluffs, we are more willing to call.
                        call_chance *= (1.0 + (opp_bluff_rate - 0.5) * (0.9 if diff == "hard" else 0.55))
                        # If bidder often survives calls with true bids, we hesitate to call.
                        call_chance *= (1.0 - (opp_defend_success_rate - 0.5) * (0.6 if diff == "hard" else 0.35))
                        # If bidder’s bluffs tend to survive (rare), hesitancy increases slightly.
                        call_chance *= (1.0 - (opp_bluff_success_rate - 0.5) * (0.25 if diff == "hard" else 0.15))

                    # Partner protection: if last bidder is partner, less likely to call
                    if bidder and partners.get(player) == bidder:
                        call_chance *= 0.6

                    # Reckless raise guard: if we and partner have 0 of the face and bid is large
                    have = dice.count(face)
                    partner_have = partner_dice.count(face) if partner_dice else 0
                    if (have + partner_have == 0) and qty > threshold_third:
                        call_chance += 0.25

                    # Clamp then late-game restraint
                    call_chance = max(0.02, min(call_chance, 0.95))

                    if diff in ("medium", "hard") and total_players_left <= 3:
                        if current_bid and current_bid[0] <= 3:
                            call_chance *= (0.45 if diff == "medium" else 0.35)
                        else:
                            call_chance *= (0.7 if diff == "medium" else 0.6)

                    # --- Plausibility filter: never call very believable low bids early ---
                    if diff in ("medium", "hard"):
                        total_dice = sum(len(d) for d in active_players.values())
                        plausible_floor = 0.09  # how "plausible" must a bid be before calling
                        # Probability that at least qty of face exist, given total dice
                        p_plausible = prob_at_least(qty, total_dice, p=1/6)

                        # If it's still statistically very likely, dampen calling chance heavily
                        if p_plausible > 0.4:  # pretty believable claim
                            scale = 0.25 if diff == "hard" else 0.4
                            call_chance *= scale

                    # Decide CALL vs RAISE
                    if random.random() < call_chance:
                        if watching or "Knight" in active_players:
                            Print(f"\n{random.choice(table_talk['call_bluff']).format(name=player)}\n")
                            Print("--- ALL DICE REVEALED ---")
                            for n, d in active_players.items():
                                Print(f"{n}: {d}")
                            Print("--------------------------\n")

                        actual_count = sum(d.count(face) for d in active_players.values())
                        if watching or "Knight" in active_players:
                            Print(f"The bid was {qty} {face}'s, there are {actual_count} {face}'s.")

                        was_truth = actual_count >= qty
                        if was_truth:
                            # Caller wrong, bidder defended
                            if watching or "Knight" in active_players:
                                Print(f"{player} loses the bluff and is OUT!")
                            if player in active_players:
                                del active_players[player]
                            eliminated_order.append(player)

                            match_memory[bidder]["defended_success"] += 1
                            record_bid_outcome(bidder, was_truth=True, was_success=True, match_memory=match_memory)
                        else:
                            # Bidder bluffing, bidder out
                            if watching or "Knight" in active_players:
                                Print(f"{bidder} was bluffing and is OUT!")
                            if bidder in active_players:
                                del active_players[bidder]
                            eliminated_order.append(bidder)

                            match_memory[bidder]["bluffs_caught"] += 1
                            record_bid_outcome(bidder, was_truth=False, was_success=False, match_memory=match_memory)

                        round_over = True

                    else:
                        # Raising behavior influenced by self confidence vs fear of next caller
                        # Self-confidence: if our truth/defend/bluff success are high, raise more
                        confidence_factor = 1.0 + (defend_success_self - 0.5) * (0.5 if diff == "hard" else 0.3) \
                                                + (bluff_success_self - 0.5) * (0.35 if diff == "hard" else 0.2)
                        conservative = qty >= max(9, threshold_third)
                        total_have = have + (partner_dice.count(face) if partner_dice else 0)

                        if total_have >= 2 and not conservative:
                            inc_raw = random.choice([1, 1, 2])
                        else:
                            inc_raw = 1 if conservative or random.random() < 0.9 else 0
                        inc = max(1, int(round(inc_raw * confidence_factor)))

                        new_qty = min(qty + inc, total_dice)
                        # Be a little more willing to bump face when confident
                        face_bump_chance = 0.65 + (confidence_factor - 1.0) * 0.2
                        new_face = face if random.random() < min(0.95, max(0.05, face_bump_chance)) else min(6, face + random.choice([0, 1]))
                        current_bid = (new_qty, new_face)
                        current_bidder = player

                        if watching or "Knight" in active_players:
                            Print(f"\n{random.choice(table_talk['raise']).format(name=player, new_qty=new_qty, new_face=new_face)}\n")

        round_start_index = (round_start_index + 1) % len(turn_order)
        press_to_continue()
        clear_cmd()

    # Final split
    survivors = list(active_players.keys())
    Print("\nFinal 2 survivors split the pot 50/50!\n")
    knight_partner = partners.get("Knight")
    if "Knight" in survivors and knight_partner in survivors:
        bonus = share // 2
        player_data["gold"] += share + bonus
        Print(f"Knight and partner {knight_partner} survive together, team bonus! Knight gets {share}+{bonus} gold, total {player_data['gold']}")
        Print(f"{knight_partner} takes {share} gold.")
    else:
        for name in survivors:
            if name == "Knight":
                player_data["gold"] += share
                Print(f"Knight gets {share} gold, total {player_data['gold']}")
            else:
                Print(f"{name} takes {share} gold.")

    # Reveal partner pairs
    Print("\nPartner pairs this match:")
    seen = set()
    for a in players:
        b = partners[a]
        if a not in seen and b not in seen:
            Print(f"{a} ↔ {b}")
            seen.add(a); seen.add(b)

    Print("\nGame Over.\n")
    Print("Elimination order, first out → last out:")
    Print(", ".join(eliminated_order) if eliminated_order else "No eliminations recorded.")

    # Persist memory (emergent personalities accumulate naturally)
    merge_match_into_global(global_memory, match_memory)
    save_ai_memory(global_memory)

# --- Main Entry ---
if __name__ == "__main__":
    player_data = {"gold": 200}
    while True:
        try:
            gold_bet = int(input("Enter your gold bet, total pot: ").strip())
            if gold_bet <= 0:
                Print("Enter a positive integer.")
                continue
            break
        except ValueError:
            Print("That's not a number, try again.")
    enemy_names = ["Jerry", "Bob", "Mark", "Lucy", "Tom", "Alice", "Sam"]
    print("Select difficulty:\n[1] Easy\n[2] Medium\n[3] Hard")
    diff_choice = input("Enter: ").strip()
    difficulty = {"1": "Easy", "2": "Medium", "3": "Hard"}.get(diff_choice, "Medium")
    Print(f"\nDifficulty set to {difficulty}")
    Print("\n--REMINDER--")
    print("Enter your bid as 'quantity face', e.g., 3 4 (Meaning you bet atleast 3 of the dice have a face of 4)\n")
    play_liars_dice(player_data, gold_bet, enemy_names, difficulty)
