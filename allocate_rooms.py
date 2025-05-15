import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random

# --- Time Setup ---
OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")
now = datetime.now(OFFICE_TIMEZONE)
this_monday = now - timedelta(days=now.weekday())
day_mapping = {
    "Monday": this_monday.date(),
    "Tuesday": (this_monday + timedelta(days=1)).date(),
    "Wednesday": (this_monday + timedelta(days=2)).date(),
    "Thursday": (this_monday + timedelta(days=3)).date(),
    "Friday": (this_monday + timedelta(days=4)).date(),
}

# --- Load Room Setup ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOMS_FILE = os.path.join(BASE_DIR, "rooms.json")
with open(ROOMS_FILE, "r") as f:
    rooms = json.load(f)

project_rooms = [r for r in rooms if r["name"] != "Oasis"]
oasis = next((r for r in rooms if r["name"] == "Oasis"), None)

# --- Allocation Function ---
def run_allocation(database_url, only=None):
    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # Clear previous allocations
        cur.execute("DELETE FROM weekly_allocations")

        # --- Project Room Allocation ---
        if only in [None, "project"]:
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences = cur.fetchall()

            used_rooms = {d: [] for d in day_mapping.values()}
            team_to_days = {}

            mon_wed = []
            tue_thu = []

            for team_name, team_size, preferred_str in team_preferences:
                preferred_days = sorted([d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping])
                if preferred_days == ["Monday", "Wednesday"]:
                    mon_wed.append((team_name, team_size, preferred_days))
                elif preferred_days == ["Tuesday", "Thursday"]:
                    tue_thu.append((team_name, team_size, preferred_days))

            def assign_combo(group, d1_label, d2_label):
                d1 = day_mapping[d1_label]
                d2 = day_mapping[d2_label]
                remaining = []

                for team_name, team_size, _ in sorted(group, key=lambda x: -x[1]):
                    available_d1 = [r for r in project_rooms if r["name"] not in used_rooms[d1] and r["capacity"] >= team_size]
                    available_d2 = [r for r in project_rooms if r["name"] not in used_rooms[d2] and r["capacity"] >= team_size]

                    exact_d1 = [r for r in available_d1 if r["capacity"] == team_size]
                    exact_d2 = [r for r in available_d2 if r["capacity"] == team_size]

                    if exact_d1 and exact_d2:
                        room1 = exact_d1[0]["name"]
                        room2 = exact_d2[0]["name"]
                    elif available_d1 and available_d2:
                        room1 = sorted(available_d1, key=lambda r: r["capacity"])[0]["name"]
                        room2 = sorted(available_d2, key=lambda r: r["capacity"])[0]["name"]
                    else:
                        remaining.append((team_name, team_size, _))
                        continue

                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room1, d1))
                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room2, d2))
                    used_rooms[d1].append(room1)
                    used_rooms[d2].append(room2)
                    team_to_days[team_name] = [d1, d2]

                return remaining

            # Primary assignment
            unplaced = assign_combo(mon_wed, "Monday", "Wednesday")
            unplaced += assign_combo(tue_thu, "Tuesday", "Thursday")

            # Final fallback: any 2 available days
            for team_name, team_size, _ in unplaced:
                placed = False
                for d1 in day_mapping.values():
                    for d2 in day_mapping.values():
                        if d1 == d2:
                            continue

                        available_d1 = [r for r in project_rooms if r["name"] not in used_rooms[d1] and r["capacity"] >= team_size]
                        available_d2 = [r for r in project_rooms if r["name"] not in used_rooms[d2] and r["capacity"] >= team_size]

                        exact_d1 = [r for r in available_d1 if r["capacity"] == team_size]
                        exact_d2 = [r for r in available_d2 if r["capacity"] == team_size]

                        if exact_d1 and exact_d2:
                            room1 = exact_d1[0]["name"]
                            room2 = exact_d2[0]["name"]
                        elif available_d1 and available_d2:
                            room1 = sorted(available_d1, key=lambda r: r["capacity"])[0]["name"]
                            room2 = sorted(available_d2, key=lambda r: r["capacity"])[0]["name"]
                        else:
                            continue

                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room1, d1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room2, d2))
                        used_rooms[d1].append(room1)
                        used_rooms[d2].append(room2)
                        team_to_days[team_name] = [d1, d2]
                        placed = True
                        break
                    if placed:
                        break

        # --- Oasis Allocation ---
        if only in [None, "oasis"]:
            cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5 FROM oasis_preferences")
            person_rows = cur.fetchall()
            random.shuffle(person_rows)

            oasis_used = {d: set() for d in day_mapping.values()}
            person_to_days = {}
            person_prefs = {
                name: [d for d in [d1, d2, d3, d4, d5] if d and d in day_mapping]
                for name, d1, d2, d3, d4, d5 in person_rows
            }

            # Round 1: assign one preferred day
            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name, date))
                        oasis_used[date].add(name)
                        person_to_days[name] = [date]
                        break

            # Round 2: assign extra preferred days
            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if name not in oasis_used[date] and len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name, date))
                        oasis_used[date].add(name)
                        person_to_days.setdefault(name, []).append(date)

        conn.commit()
        cur.close()
        conn.close()
        return True, []

    except Exception as e:
        print(f"Allocation failed: {e}")
        return False, []
