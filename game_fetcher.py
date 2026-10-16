import os
import io
import json
import time
import re
import requests
import chess.pgn
from datetime import datetime

SETUP_FILE = "game_fetcher_setup.txt"
STATE_FILE = "game_fetcher_state.json"
DEFAULT_OUTPUT_PGN = "Fetched Games.pgn"

HEADERS = {
    "User-Agent": "HIARCS-PGN-Fetcher/1.0 (Contact: brodylstone21@gmail.com)"
}


def log(message):
    """Prints messages with a standardized local timestamp."""
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {message}")


def load_config():
    chess_users = []
    lichess_users = []
    output_pgn = DEFAULT_OUTPUT_PGN
    strip_clocks_flag = True
    start_date = ""

    if os.path.exists(SETUP_FILE):
        with open(SETUP_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key, val = key.strip().upper(), val.strip()
                    if key == "CHESS_USERNAME" and val:
                        chess_users = [u.strip() for u in val.split(",") if u.strip()]
                    elif key == "LICHESS_USERNAME" and val:
                        lichess_users = [u.strip() for u in val.split(",") if u.strip()]
                    elif key == "OUTPUT_PGN" and val: 
                        output_pgn = val
                    elif key == "STRIP_CLOCKS":     
                        strip_clocks_flag = val.lower() in ("true", "yes", "1", "y")
                    elif key == "START_DATE":       
                        start_date = val
    else:
        log("=== First-Time Setup ===")
        chess_input  = input("Enter Chess.com username(s), separated by commas if multiple (leave blank if none): ").strip()
        chess_users  = [u.strip() for u in chess_input.split(",") if u.strip()]
        
        lichess_input = input("Enter Lichess username(s), separated by commas if multiple (leave blank if none): ").strip()
        lichess_users = [u.strip() for u in lichess_input.split(",") if u.strip()]

        strip_input  = input("Strip clock data between moves? [Y/n] (Default Y): ").strip().lower()
        strip_clocks_flag = False if strip_input in ("n", "false", "no") else True
        start_date   = input("Optional start date (YYYY-MM, leave blank for all-time): ").strip()

        with open(SETUP_FILE, "w", encoding="utf-8") as f:
            f.write(f"CHESS_USERNAME = {', '.join(chess_users)}\n")
            f.write(f"LICHESS_USERNAME = {', '.join(lichess_users)}\n")
            f.write(f"OUTPUT_PGN = {DEFAULT_OUTPUT_PGN}\n")
            f.write(f"STRIP_CLOCKS = {strip_clocks_flag}\n")
            f.write(f"START_DATE = {start_date}\n")
        log(f"Saved configuration to '{SETUP_FILE}'.")

    return chess_users, lichess_users, output_pgn, strip_clocks_flag, start_date


def load_state(pgn_path):
    existing_sources = set()
    chess_timestamps = {}
    lichess_timestamps = {}

    # Safety check: If the actual PGN output file is missing, invalidate the state file
    if not os.path.exists(pgn_path):
        if os.path.exists(STATE_FILE):
            log(f"Warning: Output PGN '{pgn_path}' is missing, but state file exists. Resetting state cache...")
            try:
                os.remove(STATE_FILE)
            except Exception:
                pass
        log(f"No existing PGN file found at '{pgn_path}'. A new one will be created.")
        return existing_sources, chess_timestamps, lichess_timestamps

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing_sources = set(data.get("existing_sources", []))
                chess_timestamps = data.get("chess_timestamps", {})
                lichess_timestamps = data.get("lichess_timestamps", {})
                
            log(f"Loaded state cache: {len(existing_sources)} known games tracked.")
            return existing_sources, chess_timestamps, lichess_timestamps
        except Exception:
            log("State file unreadable. Rebuilding state from local PGN...")

    # Fallback / Migration: Scan existing PGN file once if present and no valid state file
    if os.path.exists(pgn_path):
        log(f"Scanning existing PGN at '{pgn_path}' for migration...")
        with open(pgn_path, "r", encoding="utf-8", errors="ignore") as f:
            while True:
                try:
                    game = chess.pgn.read_game(f)
                    if game is None:
                        break
                    for tag in ("GameLink", "Source", "Link"):
                        val = game.headers.get(tag, "").strip()
                        if val:
                            existing_sources.add(val)
                            break
                except Exception:
                    continue
        log(f"Migration complete. Indexed {len(existing_sources)} existing games.")

    return existing_sources, chess_timestamps, lichess_timestamps


def save_state(existing_sources, chess_timestamps, lichess_timestamps):
    data = {
        "existing_sources": list(existing_sources),
        "chess_timestamps": chess_timestamps,
        "lichess_timestamps": lichess_timestamps
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def is_valid_game(game):
    variant = game.headers.get("Variant", "standard").lower()
    event   = game.headers.get("Event", "").lower()
    banned  = ["atomic", "crazyhouse", "king of the hill", "three-check",
               "horde", "antichess", "racing kings", "bughouse"]
    return not any(b in variant or b in event for b in banned)


def format_time_control_readable(tc):
    if not tc or tc == "-": return "Unknown"
    if "/" in tc: return "Daily Game"
    try:
        if "+" in tc:
            base, inc = tc.split("+")
            mins = int(base) // 60
            return f"{mins} min + {inc} sec" if mins > 0 else f"{base} sec + {inc} sec"
        else:
            secs = int(float(tc))
            return f"{secs} sec" if secs < 60 else f"{secs // 60} min"
    except Exception:
        return tc


def categorise_time_control(raw_tc):
    try:
        if "/" in raw_tc: return "Daily Game"
        secs = int(float(raw_tc.split("+")[0]))
        if secs < 180:   return "Bullet Game"
        if secs < 600:   return "Blitz Game"
        if secs < 86400: return "Rapid Game"
        return "Classical Game"
    except Exception:
        return "Blitz Game"


def strip_clocks(node):
    if node.comment:
        node.comment = re.sub(r'\[%clk[^\]]*\]', '', node.comment).strip()
    for variation in node.variations:
        strip_clocks(variation)


def build_outcome_string(reason, result):
    w, b, d = result == "1-0", result == "0-1", result == "1/2-1/2"
    outcomes = {
        "checkmate":    ("White won by checkmate",        "Black won by checkmate",        "Ended in checkmate"),
        "resignation":  ("White won by resignation",      "Black won by resignation",      "Player resigned"),
        "time":         ("White won on time",             "Black won on time",             "Ran out of time"),
        "abandonment":  ("White won due to abandonment",  "Black won due to abandonment",  "Game was abandoned"),
        "stalemate":    ("Game ended in stalemate",       "Game ended in stalemate",       "Game ended in stalemate"),
        "repetition":   ("Draw by threefold repetition",  "Draw by threefold repetition",  "Draw by threefold repetition"),
        "insufficient": ("Draw by insufficient material", "Draw by insufficient material", "Draw by insufficient material"),
        "agreement":    ("Draw by mutual agreement",      "Draw by mutual agreement",      "Draw by mutual agreement"),
        "draw":         ("Game ended in a draw",          "Game ended in a draw",          "Game ended in a draw"),
    }
    if reason in outcomes:
        row = outcomes[reason]
        return row[0] if w else row[1] if b else row[2]
    if w: return "White won"
    if b: return "Black won"
    if d: return "Game ended in a draw"
    return "Game concluded"


def detect_lichess_reason(game, raw_pgn_text):
    termination = game.headers.get("Termination", "").lower()
    result      = game.headers.get("Result", "*")

    if "time forfeit" in termination:
        return "time"
    if "abandon" in termination:
        return "abandonment"

    if result == "1/2-1/2":
        raw_lower = raw_pgn_text.lower()
        if "stalemate"    in raw_lower: return "stalemate"
        if "repetition"   in raw_lower: return "repetition"
        if "insufficient" in raw_lower: return "insufficient"
        if "agreement"    in raw_lower: return "agreement"
        return "draw"

    if result in ("1-0", "0-1"):
        try:
            board = game.board()
            node  = game
            while node.variations:
                node = node.variations[0]
                board.push(node.move)
            if board.is_checkmate():
                return "checkmate"
        except Exception:
            pass
        return "resignation"

    return None


def format_comment_chesscom(game, raw_tc):
    readable_tc = format_time_control_readable(raw_tc)
    termination = game.headers.get("Termination", "").lower()
    result      = game.headers.get("Result", "*")

    if   "checkmate"    in termination or "mate"    in termination: reason = "checkmate"
    elif "resign"       in termination:                             reason = "resignation"
    elif "time"         in termination or "forfeit" in termination: reason = "time"
    elif "abandon"      in termination:                             reason = "abandonment"
    elif "stalemate"    in termination:                             reason = "stalemate"
    elif "repetition"   in termination:                             reason = "repetition"
    elif "insufficient" in termination:                             reason = "insufficient"
    elif "agreement"    in termination:                             reason = "agreement"
    elif "draw"         in termination or result == "1/2-1/2":      reason = "draw"
    else:                                                           reason = None

    return f"Time Control: {readable_tc} | Result: {build_outcome_string(reason, result)}"


def format_comment_lichess(game, raw_tc, raw_pgn_text):
    readable_tc = format_time_control_readable(raw_tc)
    result      = game.headers.get("Result", "*")
    reason      = detect_lichess_reason(game, raw_pgn_text)
    return f"Time Control: {readable_tc} | Result: {build_outcome_string(reason, result)}"


def clean_game_headers(game, platform_name, source_url, is_rated=True, raw_pgn_text=""):
    raw_tc    = game.headers.get("TimeControl", "")
    variant   = game.headers.get("Variant", "standard").lower()
    event_low = game.headers.get("Event", "").lower()

    game.headers["Site"]     = platform_name
    game.headers["Platform"] = platform_name

    for old_tag in ("Source", "Link"):
        if old_tag in game.headers:
            del game.headers[old_tag]
    if source_url:
        game.headers["GameLink"] = source_url

    if platform_name == "Lichess" and ("casual" in event_low or "unrated" in event_low):
        is_rated = False

    category = categorise_time_control(raw_tc)
    prefix   = "Chess960 " if ("960" in variant or "fischer" in variant) else ""
    game.headers["Event"] = f"{'Rated' if is_rated else 'Unrated'} {prefix}{category}"

    if platform_name == "Chess.com":
        comment = format_comment_chesscom(game, raw_tc)
    else:
        comment = format_comment_lichess(game, raw_tc, raw_pgn_text)

    if game.variations:
        game.variations[0].starting_comment = comment
    else:
        game.comment = comment

    for tag in ("TimeControl", "GameId", "LichessID", "ECOUrl"):
        if tag in game.headers:
            del game.headers[tag]


def fetch_chess_com_games(usernames, existing_sources, strip_clocks_flag, start_date, chess_timestamps):
    if not usernames: return []
    all_new_games = []
    current_run_time = time.time()

    for username in usernames:
        user_key = username.lower()
        last_fetch = chess_timestamps.get(user_key, 0)
        is_new_user = (user_key not in chess_timestamps)

        if is_new_user:
            log(f"--- New Chess.com user detected ('{username}'). Fetching full history... ---")
        else:
            log(f"--- Connecting to Chess.com for '{username}' (Incremental) ---")

        resp = requests.get(
            f"https://api.chess.com/pub/player/{user_key}/games/archives",
            headers=HEADERS
        )
        if resp.status_code != 200:
            log(f"Failed to reach Chess.com for {username} (Status {resp.status_code})")
            continue

        archive_urls = resp.json().get("archives", [])

        cutoff_dt = None
        if last_fetch > 0 and not start_date and not is_new_user:
            cutoff_dt = datetime.fromtimestamp(last_fetch - 3 * 86400)

        start_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
        total_added = 0

        for url in archive_urls:
            parts = url.split("/")
            year, month = int(parts[-2]), int(parts[-1])
            
            if start_tuple and (year, month) < start_tuple:
                continue
            if cutoff_dt and (year, month) < (cutoff_dt.year, cutoff_dt.month):
                continue

            month_resp = requests.get(url, headers=HEADERS)
            time.sleep(0.2)
            if month_resp.status_code != 200:
                continue

            added = 0
            for g in month_resp.json().get("games", []):
                pgn_text   = g.get("pgn")
                source_url = g.get("url", "").strip()

                if not pgn_text: continue
                if source_url and source_url in existing_sources: continue

                try:
                    game = chess.pgn.read_game(io.StringIO(pgn_text))
                    if game is None or not is_valid_game(game): continue

                    clean_game_headers(
                        game,
                        platform_name="Chess.com",
                        source_url=source_url,
                        is_rated=g.get("rated", True)
                    )
                    if strip_clocks_flag: strip_clocks(game)

                    all_new_games.append(game)
                    if source_url: existing_sources.add(source_url)
                    added += 1
                    total_added += 1
                except Exception:
                    continue

            log(f"  [{username} - {year}/{month:02d}] Added {added} valid games.")
        
        chess_timestamps[user_key] = current_run_time
        log(f"Total new games for {username}: {total_added}")

    return all_new_games


def fetch_lichess_games(usernames, existing_sources, strip_clocks_flag, start_date, lichess_timestamps):
    if not usernames: return []
    all_new_games = []
    current_run_time = time.time()

    for username in usernames:
        user_key = username.lower()
        last_fetch = lichess_timestamps.get(user_key, 0)
        is_new_user = (user_key not in lichess_timestamps)

        if is_new_user:
            log(f"--- New Lichess user detected ('{username}'). Fetching full history... ---")
        else:
            log(f"--- Connecting to Lichess for '{username}' (Incremental) ---")

        url = f"https://lichess.org/api/games/user/{username}?evals=0&clocks=1"

        effective_since = 0
        if start_date:
            try:
                dt = datetime.strptime(start_date, "%Y-%m")
                effective_since = int(dt.timestamp() * 1000)
            except Exception:
                pass
        elif last_fetch > 0 and not is_new_user:
            effective_since = int((last_fetch - 3 * 86400) * 1000)

        if effective_since > 0:
            url += f"&since={effective_since}"

        log(f"Downloading Lichess export for {username}...")
        resp = requests.get(url, headers={**HEADERS, "Accept": "application/x-chess-pgn"})
        if resp.status_code != 200:
            log(f"Failed to fetch Lichess games for {username} (Status {resp.status_code})")
            continue

        raw_blocks = []
        current_block_lines = []
        for line in resp.text.splitlines():
            if line.startswith("[Event ") and current_block_lines:
                raw_blocks.append("\n".join(current_block_lines))
                current_block_lines = []
            current_block_lines.append(line)
        if current_block_lines:
            raw_blocks.append("\n".join(current_block_lines))

        new_count = 0
        for raw_block in raw_blocks:
            try:
                game = chess.pgn.read_game(io.StringIO(raw_block))
                if game is None: continue
                if not is_valid_game(game): continue

                source_url = game.headers.get("Site", "").strip()
                if source_url and source_url in existing_sources: continue

                clean_game_headers(
                    game,
                    platform_name="Lichess",
                    source_url=source_url,
                    raw_pgn_text=raw_block
                )
                if strip_clocks_flag: strip_clocks(game)

                all_new_games.append(game)
                if source_url: existing_sources.add(source_url)
                new_count += 1
            except Exception:
                continue

        lichess_timestamps[user_key] = current_run_time
        log(f"Finished Lichess for {username} | New added: {new_count}.")
        time.sleep(0.5)

    return all_new_games


def main():
    chess_users, lichess_users, output_pgn, strip_clocks_flag, start_date = load_config()
    log(
        f"Active config -> Chess.com: '{', '.join(chess_users) or 'Disabled'}' | "
        f"Lichess: '{', '.join(lichess_users) or 'Disabled'}' | Output: '{output_pgn}'"
    )

    existing_sources, chess_timestamps, lichess_timestamps = load_state(output_pgn)

    chess_games   = fetch_chess_com_games(chess_users,   existing_sources, strip_clocks_flag, start_date, chess_timestamps)
    lichess_games = fetch_lichess_games(lichess_users, existing_sources, strip_clocks_flag, start_date, lichess_timestamps)

    all_new = chess_games + lichess_games
    if not all_new:
        log(f"No new games found. '{output_pgn}' is already up to date!")
        save_state(existing_sources, chess_timestamps, lichess_timestamps)
        return

    log(f"Appending {len(all_new)} new games to '{output_pgn}'...")
    with open(output_pgn, "a", encoding="utf-8") as f:
        for game in all_new:
            print(game, file=f, end="\n\n")

    save_state(existing_sources, chess_timestamps, lichess_timestamps)
    log(f"Done! '{output_pgn}' updated successfully.")


if __name__ == "__main__":
    main()
