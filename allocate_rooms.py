import psycopg2
import json
import os
import random
from datetime import datetime, timedelta
import pytz

# --- Time Setup ---
OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")
now = datetime.now(OFFICE_TIMEZONE)
this_monday = now - timedelta(days=now.weekday())
day_mapping = {
    "Monday": this_monday.date(),
    "Tuesday": (this_monday + timedelta(days=1)).date(),
    "Wednesday": (this_monday + timedelta(days=2)).date(),
    "Thursday": (this_monday + timedelta(days=3)).date(),
}

# --- Load Room Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, "rooms.json")
with open(ROOMS_FILE, "r") as f:
    rooms = json.load(f)

project_rooms = [r for r in rooms if r["name"] != "Oasis"]
oasis = next((r for r in rooms if r["name"] == "Oasis"), None)

def run_allocation(database_url):
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # Clear old allocations
        cur.execute("DELETE FROM weekly_allocations")

        # --- Load Team Preferences ---
        cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
        team_preferences = cur.fetchall()
        random.shuffle(team_preferences)

        used_rooms = {d: [] for d in day_mapping.values()}
        team_to_days = {}
        placed_once = set()
        all_team_names = {team_name for team_name, _, _ in team_preferences}

        # --- First Round: Ensure one placement per team ---
        for team_name, team_size, preferred_str in team_preferences:
            preferred_days = [d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping]
            random.shuffle(preferred_days)
            for day in preferred_days:
                date = day_mapping[day]
                available = [
                    r for r in project_rooms
                    if r["capacity"] >= team_size and r["name"] not in used_rooms[date]
                ]
                available = sorted(available, key=lambda r: r["capacity"])
                if available:
                    room = available[0]["name"]
                    cur.execute(
                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                        (team_name, room, date)
                    )
                    used_rooms[date].append(room)
                    placed_once.add(team_name)
                    team_to_days.setdefault(team_name, []).append(date)
                    break

        # --- Second Round: Try placing again on other preferred day ---
        for team_name, team_size, preferred_str in team_preferences:
            preferred_days = [d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping]
            random.shuffle(preferred_days)
            for day in preferred_days:
                date = day_mapping[day]
                if team_name in team_to_days and date in team_to_days[team_name]:
                    continue  # already placed on this day
                available = [
                    r for r in project_rooms
                    if r["capacity"] >= team_size and r["name"] not in used_rooms[date]
                ]
                available = sorted(available, key=lambda r: r["capacity"])
                if available:
                    room = available[0]["name"]
                    cur.execute(
                        "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                        (team_name, room, date)
                    )
                    used_rooms[date].append(room)
                    team_to_days.setdefault(team_name, []).append(date)
                    break

        # --- Oasis Allocation ---
        if oasis:
            oasis_used = {d: [] for d in day_mapping.values()}
            cur.execute("SELECT person_name, preferred_days FROM oasis_preferences")
            person_rows = cur.fetchall()
            random.shuffle(person_rows)

            for person_name, preferred_str in person_rows:
                preferred_days = [d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping]
                for day in preferred_days:
                    date = day_mapping[day]
                    if len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute(
                            "INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                            (person_name, "Oasis", date)
                        )
                        oasis_used[date].append(person_name)

        conn.commit()
        cur.close()
        conn.close()

        # --- Return unplaced teams ---
        unplaced = all_team_names - placed_once
        return True, sorted(unplaced)

    except Exception as e:
        print(f"Allocation failed: {e}")
        return False, []
