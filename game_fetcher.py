import os
import io
import time
import re
import requests
import chess.pgn
from datetime import datetime, timedelta

SETUP_FILE = "game_fetcher_setup.txt"
DEFAULT_OUTPUT_PGN = "My Online Games.pgn"

HEADERS = {
    "User-Agent": "HIARCS-PGN-Fetcher/1.0 (Contact: brodylstone21@gmail.com)"
}

def load_config():
    """Loads configuration or runs first-time interactive setup."""
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
                    if key == "CHESS_USERNAME": chess_user = val
                    elif key == "LICHESS_USERNAME": lichess_user = val
                    elif key == "OUTPUT_PGN" and val: output_pgn = val
                    elif key == "STRIP_CLOCKS": strip_clocks_flag = val.lower() in ("true", "yes", "1", "y")
                    elif key == "START_DATE": start_date = val
                            
    if not os.path.exists(SETUP_FILE):
        print("=== First-Time Setup ===")
        chess_user = input("Enter your Chess.com username (leave blank if none): ").strip()
        lichess_user = input("Enter your Lichess username (leave blank if none): ").strip()
        
        strip_input = input("Strip clock data between moves? [Y/n] (Default is Y): ").strip().lower()
        strip_clocks_flag = False if strip_input in ("n", "false", "no") else True
            
        start_date = input("Optional start date (YYYY-MM, leave blank for all-time): ").strip()
        
        with open(SETUP_FILE, "w", encoding="utf-8") as f:
            f.write(f"CHESS_USERNAME = {chess_user}\n")
            f.write(f"LICHESS_USERNAME = {lichess_user}\n")
            f.write(f"OUTPUT_PGN = {DEFAULT_OUTPUT_PGN}\n")
            f.write(f"STRIP_CLOCKS = {strip_clocks_flag}\n")
            f.write(f"START_DATE = {start_date}\n")
        print(f"\nSaved configuration to '{SETUP_FILE}'.\n")
        
    return chess_user, lichess_user, output_pgn, strip_clocks_flag, start_date

def get_existing_sites(pgn_path):
    """Loads already saved game URLs/IDs and checks local file modification time."""
    existing_sites = set()
    last_modified_timestamp = 0
    
    if not os.path.exists(pgn_path):
        print(f"No existing PGN file found at '{pgn_path}'. A new one will be created.")
        return existing_sites, 0
    
    last_modified_timestamp = os.path.getmtime(pgn_path)
    print(f"Reading existing local database from {pgn_path}...")
    
    with open(pgn_path, "r", encoding="utf-8", errors="ignore") as f:
        while True:
            try:
                game = chess.pgn.read_game(f)
                if game is None: break
                site = game.headers.get("Site")
                if site and site.strip():
                    existing_sites.add(site.strip())
            except Exception:
                continue
                
    print(f"Found {len(existing_sites)} existing games already saved locally.")
    return existing_sites, last_modified_timestamp

def is_valid_game(game):
    """Filters out non-standard variants (Bughouse, Atomic, Crazyhouse, etc.)."""
    variant = game.headers.get("Variant", "standard").lower()
    event = game.headers.get("Event", "").lower()
    banned = ["atomic", "crazyhouse", "king of the hill", "three-check", "horde", "antichess", "racing kings", "bughouse"]
    return not any(b in variant or b in event for b in banned)

def format_time_control_readable(tc):
    """Converts raw time control strings into human-readable descriptions."""
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

def format_comment(game, raw_tc):
    """Generates standardized result commentary for starting comments."""
    readable_tc = format_time_control_readable(raw_tc)
    termination = game.headers.get("Termination", "").strip().lower()
    result = game.headers.get("Result", "*")
    
    if "checkmate" in termination:
        outcome = "White won by checkmate" if result == "1-0" else "Black won by checkmate" if result == "0-1" else "Ended in checkmate"
    elif "resign" in termination:
        outcome = "White won by resignation" if result == "1-0" else "Black won by resignation" if result == "0-1" else "Player resigned"
    elif "time" in termination:
        outcome = "White won on time" if result == "1-0" else "Black won on time" if result == "0-1" else "Ran out of time"
    elif "abandon" in termination:
        outcome = "White won due to abandonment" if result == "1-0" else "Black won due to abandonment" if result == "0-1" else "Game was abandoned"
    elif "draw" in termination or result == "1/2-1/2":
        outcome = "Game ended in a draw"
    else:
        outcome = termination.capitalize() if termination else ("White won" if result == "1-0" else "Black won" if result == "0-1" else "Draw")

    return f"Time Control: {readable_tc} | Result: {outcome}"

def strip_clocks(node):
    """Recursively removes internal clock timestamps from move annotations."""
    if node.comment:
        node.comment = re.sub(r'\[%clk[^\]]*\]', '', node.comment).strip()
    for variation in node.variations:
        strip_clocks(variation)

def clean_game_headers(game, platform_name, is_rated=True):
    """Standardizes PGN headers and adds clean result commentary."""
    game.headers["Platform"] = platform_name
    raw_tc = game.headers.get("TimeControl", "")
    variant = game.headers.get("Variant", "standard").lower()
    event_lower = game.headers.get("Event", "").lower()
    
    if platform_name == "Lichess" and ("casual" in event_lower or "unrated" in event_lower):
        is_rated = False

    category = "Blitz Game"
    try:
        if "/" in raw_tc:
            category = "Daily Game"
        else:
            secs = int(float(raw_tc.split("+")[0]))
            if secs < 180: category = "Bullet Game"
            elif secs < 600: category = "Blitz Game"
            elif secs < 86400: category = "Rapid Game"
            else: category = "Classical Game"
    except Exception:
        pass

    prefix = "Chess960 " if "960" in variant or "fischer" in variant else ""
    game.headers["Event"] = f"{'Rated' if is_rated else 'Unrated'} {prefix}{category}"
    
    comment = format_comment(game, raw_tc)
    if game.variations: game.variations[0].starting_comment = comment
    else: game.comment = comment
    
    if "TimeControl" in game.headers: del game.headers["TimeControl"]

def fetch_chess_com_games(username, existing_sites, strip_clocks_flag, start_date, last_modified):
    """Fetches games from Chess.com, checking only recent archives if incremental."""
    if not username: return []
    print(f"\n--- Connecting to Chess.com for '{username}' ---")
    
    resp = requests.get(f"https://api.chess.com/pub/player/{username.lower()}/games/archives", headers=HEADERS)
    if resp.status_code != 200: return []
        
    archive_urls = resp.json().get("archives", [])
    
    # If file exists and no manual start date, limit to last 3 archives (covers current + safety overlap)
    if last_modified > 0 and not start_date:
        archive_urls = archive_urls[-3:]
        print(f"Incremental mode: Checking last {len(archive_urls)} monthly archive(s)...")
    else:
        print(f"Found {len(archive_urls)} monthly archive periods. Processing all...")
    
    start_tuple = tuple(map(int, start_date.split("-"))) if start_date else None
    new_games = []
    total_added = 0
    
    for url in archive_urls:
        parts = url.split('/')
        year, month = parts[-2], parts[-1]
        if start_tuple and (int(year), int(month)) < start_tuple: continue
            
        month_resp = requests.get(url, headers=HEADERS)
        time.sleep(0.2)
        if month_resp.status_code != 200: continue
            
        added = 0
        for g in month_resp.json().get("games", []):
            pgn_text = g.get("pgn")
            unique_url = g.get("url", "").strip()
            if not pgn_text or (unique_url and unique_url in existing_sites): continue
            
            try:
                game = chess.pgn.read_game(io.StringIO(pgn_text))
                if game is None or not is_valid_game(game): continue
                if unique_url: game.headers["Site"] = unique_url
                
                clean_game_headers(game, "Chess.com", is_rated=g.get("rated", True))
                if strip_clocks_flag: strip_clocks(game)
                
                new_games.append(game)
                if unique_url: existing_sites.add(unique_url)
                added += 1
                total_added += 1
            except Exception:
                continue
        print(f"[{year}/{month}] Checked archive -> Added {added} valid games.")
        
    print(f"Total new standard/960 games retrieved from Chess.com: {total_added}")
    return new_games

def fetch_lichess_games(username, existing_sites, strip_clocks_flag, start_date, last_modified):
    """Fetches games from Lichess with a safe 3-day overlap buffer for incremental runs."""
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
        # Subtract 3 days (259,200 seconds) as a safety buffer to ensure zero missed games
        three_days_ago_ts = last_modified - (3 * 86400)
        effective_since = int(three_days_ago_ts * 1000)
        print("Incremental mode: Fetching Lichess games with a 3-day safety overlap buffer...")

    if effective_since > 0:
        url += f"&since={effective_since}"

    print("Downloading Lichess game export (this may take a few seconds)...")
    resp = requests.get(url, headers={**HEADERS, "Accept": "application/x-chess-pgn"})
    if resp.status_code != 200:
        print(f"Failed to fetch Lichess games (Status {resp.status_code})")
        return []

    new_games = []
    stream = io.StringIO(resp.text)
    processed = 0
    
    while True:
        try:
            game = chess.pgn.read_game(stream)
            if game is None: break
            processed += 1
            
            if not is_valid_game(game): continue
            site = game.headers.get("Site", "").strip()
            if site and site in existing_sites: continue
                
            clean_game_headers(game, "Lichess")
            if strip_clocks_flag: strip_clocks(game)
            
            new_games.append(game)
            if site: existing_sites.add(site)
        except Exception:
            continue
            
    print(f"Finished Lichess. Total processed in window: {processed} | New added: {len(new_games)}.")
    return new_games

def main():
    chess_user, lichess_user, output_pgn, strip_clocks_flag, start_date = load_config()
    print(f"Active config -> Chess.com: '{chess_user or 'Disabled'}' | Lichess: '{lichess_user or 'Disabled'}' | Output: '{output_pgn}'")
    
    existing_sites, last_modified = get_existing_sites(output_pgn)
    
    chess_games = fetch_chess_com_games(chess_user, existing_sites, strip_clocks_flag, start_date, last_modified)
    lichess_games = fetch_lichess_games(lichess_user, existing_sites, strip_clocks_flag, start_date, last_modified)
    
    all_new = chess_games + lichess_games
    if not all_new:
        print(f"\nNo new games found. Your '{output_pgn}' file is already up to date!")
        return
        
    print(f"\nAppending {len(all_new)} new games to {output_pgn}...")
    with open(output_pgn, "a", encoding="utf-8") as f:
        for game in all_new:
            print(game, file=f, end="\n\n")
    print(f"Success! {output_pgn} updated cleanly.")

if __name__ == "__main__":
    main()
