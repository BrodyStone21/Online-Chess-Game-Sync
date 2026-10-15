import os
import io
import time
import re
import requests
import chess.pgn
from datetime import datetime

SETUP_FILE = "game_fetcher_setup.txt"
DEFAULT_OUTPUT_PGN = "My Online Games.pgn"

HEADERS = {
    "User-Agent": "HIARCS-PGN-Fetcher/1.0 (Contact: brodylstone21@gmail.com)"
}


def load_config():
    chess_user = ""
    lichess_user = ""
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
                    if key == "CHESS_USERNAME":     chess_user = val
                    elif key == "LICHESS_USERNAME": lichess_user = val
                    elif key == "OUTPUT_PGN" and val: output_pgn = val
                    elif key == "STRIP_CLOCKS":     strip_clocks_flag = val.lower() in ("true", "yes", "1", "y")
                    elif key == "START_DATE":       start_date = val
    else:
        print("=== First-Time Setup ===")
        chess_user   = input("Enter your Chess.com username (leave blank if none): ").strip()
        lichess_user = input("Enter your Lichess username (leave blank if none): ").strip()
        strip_input  = input("Strip clock data between moves? [Y/n] (Default Y): ").strip().lower()
        strip_clocks_flag = False if strip_input in ("n", "false", "no") else True
        start_date   = input("Optional start date (YYYY-MM, leave blank for all-time): ").strip()

        with open(SETUP_FILE, "w", encoding="utf-8") as f:
            f.write(f"CHESS_USERNAME = {chess_user}\n")
            f.write(f"LICHESS_USERNAME = {lichess_user}\n")
            f.write(f"OUTPUT_PGN = {DEFAULT_OUTPUT_PGN}\n")
            f.write(f"STRIP_CLOCKS = {strip_clocks_flag}\n")
            f.write(f"START_DATE = {start_date}\n")
        print(f"\nSaved configuration to '{SETUP_FILE}'.\n")

    return chess_user, lichess_user, output_pgn, strip_clocks_flag, start_date


def get_existing_sources(pgn_path):
    existing_sources = set()
    if not os.path.exists(pgn_path):
        print(f"No existing PGN file found at '{pgn_path}'. A new one will be created.")
        return existing_sources, 0

    last_modified = os.path.getmtime(pgn_path)
    print(f"Reading existing local database from '{pgn_path}'...")

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

    print(f"Found {len(existing_sources)} existing games already saved locally.")
    return existing_sources, last_modified


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


def fetch_chess_com_games(username, existing_sources, strip_clocks_flag, start_date, last_modified):
    if not username: return []
    print(f"\n--- Connecting to Chess.com for '{username}' ---")

    resp = requests.get(
        f"https://api.chess.com/pub/player/{username.lower()}/games/archives",
        headers=HEADERS
    )
    if resp.status_code != 200:
        print(f"Failed to reach Chess.com (Status {resp.status_code})")
        return []

    archive_urls = resp.json().get("archives", [])

    if last_modified > 0 and not start_date:
        archive_urls = archive_urls[-6:]
        print(f"Incremental mode: Checking last {len(archive_urls)} monthly archive(s)...")
    else:
        print(f"Found {len(archive_urls)} monthly archive periods. Processing all...")

    start_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
    new_games, total_added = [], 0

    for url in archive_urls:
        parts = url.split("/")
        year, month = parts[-2], parts[-1]
        if start_tuple and (int(year), int(month)) < start_tuple:
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

                new_games.append(game)
                if source_url: existing_sources.add(source_url)
                added += 1
                total_added += 1
            except Exception:
                continue

        print(f"  [{year}/{month}] Added {added} valid games.")

    print(f"Total new games from Chess.com: {total_added}")
    return new_games


def fetch_lichess_games(username, existing_sources, strip_clocks_flag, start_date, last_modified):
    if not username: return []
    print(f"\n--- Connecting to Lichess for '{username}' ---")

    url = f"https://lichess.org/api/games/user/{username}?evals=0&clocks=1"

    effective_since = 0
    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m")
            effective_since = int(dt.timestamp() * 1000)
        except Exception:
            pass
    elif last_modified > 0:
        effective_since = int((last_modified - 3 * 86400) * 1000)
        print("Incremental mode: Fetching with a 3-day safety overlap buffer...")

    if effective_since > 0:
        url += f"&since={effective_since}"

    print("Downloading Lichess game export (this may take a minute or two)...")
    resp = requests.get(url, headers={**HEADERS, "Accept": "application/x-chess-pgn"})
    if resp.status_code != 200:
        print(f"Failed to fetch Lichess games (Status {resp.status_code})")
        return []

    raw_blocks = []
    current_block_lines = []
    for line in resp.text.splitlines():
        if line.startswith("[Event ") and current_block_lines:
            raw_blocks.append("\n".join(current_block_lines))
            current_block_lines = []
        current_block_lines.append(line)
    if current_block_lines:
        raw_blocks.append("\n".join(current_block_lines))

    new_games = []
    processed = 0

    for raw_block in raw_blocks:
        try:
            game = chess.pgn.read_game(io.StringIO(raw_block))
            if game is None: continue
            processed += 1

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

            new_games.append(game)
            if source_url: existing_sources.add(source_url)

        except Exception:
            continue

    print(f"Finished Lichess. Processed {processed} | New added: {len(new_games)}.")
    return new_games


def main():
    chess_user, lichess_user, output_pgn, strip_clocks_flag, start_date = load_config()
    print(
        f"Active config -> Chess.com: '{chess_user or 'Disabled'}' | "
        f"Lichess: '{lichess_user or 'Disabled'}' | Output: '{output_pgn}'"
    )

    existing_sources, last_modified = get_existing_sources(output_pgn)

    chess_games   = fetch_chess_com_games(chess_user,   existing_sources, strip_clocks_flag, start_date, last_modified)
    lichess_games = fetch_lichess_games(lichess_user, existing_sources, strip_clocks_flag, start_date, last_modified)

    all_new = chess_games + lichess_games
    if not all_new:
        print(f"\nNo new games found. '{output_pgn}' is already up to date!")
        return

    print(f"\nAppending {len(all_new)} new games to '{output_pgn}'...")
    with open(output_pgn, "a", encoding="utf-8") as f:
        for game in all_new:
            print(game, file=f, end="\n\n")
    print(f"Done! '{output_pgn}' updated successfully.")


if __name__ == "__main__":
    main()
