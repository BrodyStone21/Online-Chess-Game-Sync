# Online Chess Game Sync

Python script designed to automatically fetch, clean, and sync your online chess games from **Chess.com** and **Lichess** into a single, master PGN database. 

You can plug the PGN into almost any chess software, I personally use HIARCS Chess Explorer Pro, but this will also work with ChessBase, EnCroissant, Scid, ChessX, etc.

---

## Screenshots

### 1. Initial Terminal Setup
*The script interactively generates your configuration on first run.*
<img width="921" height="596" alt="CleanShot 2026-10-15 at 14 27 41" src="https://github.com/user-attachments/assets/4c8446c4-9826-4fb8-b61a-9d5886d0f2de" />

### 2. Game Fetching Live Progress
*The script displays a live progress status when fetching games.*
<img width="869" height="562" alt="CleanShot 2026-10-15 at 15 15 09" src="https://github.com/user-attachments/assets/4555afdb-a2e3-4650-9cf3-52fb98daa924" />
<img width="865" height="558" alt="CleanShot 2026-10-15 at 15 16 44" src="https://github.com/user-attachments/assets/72195c73-b268-46da-b26f-23fa00e0a9ce" />
<img width="876" height="562" alt="CleanShot 2026-10-15 at 15 14 04" src="https://github.com/user-attachments/assets/a81e575e-d423-4290-af0c-e493df767396" />

### 3. Using PGN With Chess Software (Pictured is HIARCS Chess Explorer Pro)
*Ready to use for move tree exploration, game analysis, annotation, and anything else you regularly use your chess software for.*
<img width="826" height="141" alt="CleanShot 2026-10-15 at 17 26 47" src="https://github.com/user-attachments/assets/ef582724-69fd-4a68-9c56-16b2865c628c" />
<img width="586" height="576" alt="CleanShot 2026-10-15 at 17 27 20" src="https://github.com/user-attachments/assets/31df79dd-750f-4dfe-b882-3b746225f51c" />

---

## Key Features

* **Configuration on First Run:** On first run, the script prompts you for your username for both platforms, clock-stripping preference, and an optional date filter. Settings can later be modified by editing the `game_fetcher_setup.txt` file.
* **Time Control Classification:** Groups your games into standard categories (`Bullet`, `Blitz`, `Rapid`, `Classical`, or `Daily`) and respects modern online thresholds (e.g., 10-minute games correctly map to Rapid).
* **Subsequent Runs Skip Existing Games:** Detects your local database modification time and targets only recent archives/timestamps (Lichess games will have a 3-day overlap buffer to make sure no games are missed since the export process between Lichess and Chess.com is different) while using URL deduplication.
* **Rated/Unrated Differentiation:** Easily see if a specific game was rated or unrated by looking at the `Event` tag.
* **Preceeding Result Comments:** Puts the reason for game terminations in the starting comment (e.g., `Time Control: 10 min + 0 sec | Result: White won by resignation`). Note: Lichess does not include resignation or abandonment reasons in their PGN exports, so all non-checkmate, non-time, non-draw Lichess results are listed as resignation."
* **Variant Filtering:** Automatically filters out variant games (Atomic, Crazyhouse, King of the Hill, etc), but keeps your Chess960/Fischer Random games.
* **Optional Date Range Filtering:** Set a `START_DATE` (`YYYY-MM`) in your configuration if you only want to pull games from a specific point onward (or leave blank to pull all games).

---

## Performance & Speed
Pulling thousands of games across both platforms takes less than two minutes:
* **Chess.com:** ~4,900 games processed across 32 monthly archives in 20 seconds.
* **Lichess:** ~1,800 games exported in 75 seconds.
* **Incremental Runs:** Subsequent daily updates take less than 10 seconds since duplicate checking skips previously saved games.

## Requirements
* Python 3.x
* The `requests` and `python-chess` libraries. You can install them via terminal:
  ```bash
  pip install requests python-chess

---

## How to Use
Open Terminal (if you're on Linux or Mac) or Command Prompt (if you're on Windows), navigate to the directory your script is stored, for example, `cd Desktop/Game_Fetcher`, then run the python script `python3 game_fetcher.py`. Depending on the system, you may need to remove the `3`, `python game_fetcher.py`. 

If you decide you'd like to change options like your username, clock stripping setting, etc, then you can modify them in the `game_fetcher_setup.txt` file. 

It's that easy!

Let me know if you have any issues or would like to request new features. I'm afraid creating a GUI is outside my wheelhouse, so this will only be a text based program for the foreseeable future. I hope you enjoy it!
