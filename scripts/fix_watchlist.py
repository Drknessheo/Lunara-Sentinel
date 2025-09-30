
import asyncio
import re
import sys
from pathlib import Path

# Add the project root to the Python path to allow importing from 'src'
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src import db

# The user ID to fix
USER_ID = 6284071528

async def main():
    """
    Connects to the database, fetches the user's watchlist, cleans it,
    and saves it back.
    """
    print("--- Watchlist Repair Script ---")
    
    try:
        # 1. Get the current, corrupted settings
        settings = await db.get_user_effective_settings(USER_ID)
        corrupted_watchlist = settings.get('watchlist')
        
        if not corrupted_watchlist:
            print(f"No watchlist found for user {USER_ID}. Nothing to do.")
            return

        print(f"Found corrupted watchlist: \n'{corrupted_watchlist}'")

        # 2. Clean the watchlist string
        # Use regex to find all valid-looking symbols (e.g., BTCUSDT)
        symbols = re.findall(r'[A-Z]{3,10}USDT', corrupted_watchlist.upper())
        # Create a clean, sorted, comma-separated string with no duplicates
        clean_watchlist = ",".join(sorted(list(set(symbols))))

        if not clean_watchlist:
            print("Could not find any valid symbols. Setting watchlist to empty.")
        
        print(f"\nCleaned watchlist: \n'{clean_watchlist}'")

        # 3. Save the clean watchlist back to the database
        await db.update_user_setting(USER_ID, "watchlist", clean_watchlist)
        
        print(f"\nSuccessfully repaired watchlist for user {USER_ID} in the database.")

    except Exception as e:
        print(f"\nAn error occurred: {e}")
        print("Please ensure the database file exists at 'lunessa.db' and is accessible.")

if __name__ == "__main__":
    # Since db functions are async, we need to run the main function in an event loop.
    asyncio.run(main())
