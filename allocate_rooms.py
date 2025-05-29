import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random
from itertools import combinations

# --- Time Setup ---
OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")

def get_day_mapping():
    now = datetime.now(OFFICE_TIMEZONE)
    this_monday = now - timedelta(days=now.weekday())
    return {
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

def run_allocation(database_url, only=None):
    day_mapping = get_day_mapping()

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # --- Clear relevant previous allocations ---
        if only == "project":
            cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
        elif only == "oasis":
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            if cur.fetchone()[0] == 0:
                print("No oasis preferences submitted. Skipping allocation.")
                conn.rollback()
                cur.close()
                conn.close()
                return False, ["No oasis preferences to allocate."]
            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'")
        else:
            cur.execute("DELETE FROM weekly_allocations")

        # --- Project Room Allocation ---
        if only in [None, "project"]:
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences = cur.fetchall()

            used_rooms = {d: [] for d in day_mapping.values()}
            team_to_days = {}

            mon_wed = []
            tue_thu = []
            unplaced_teams = []

            for team_name, team_size, preferred_str in team_preferences:
                preferred_days = sorted([d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping])
                if preferred_days == ["Monday", "Wednesday"]:
                    mon_wed.append((team_name, team_size, preferred_days))
                elif preferred_days == ["Tuesday", "Thursday"]:
                    tue_thu.append((team_name, team_size, preferred_days))
                else:
                    unplaced_teams.append((team_name, team_size, None))

            def assign_combo(group, d1_label, d2_label):
                d1 = day_mapping[d1_label]
                d2 = day_mapping[d2_label]
                remaining = []

                for team_name, team_size, _ in group:
                    available_rooms = [
                        r for r in project_rooms
                        if r["name"] not in used_rooms[d1]
                        and r["name"] not in used_rooms[d2]
                        and r["capacity"] >= team_size
                    ]
                    if available_rooms:
                        room = sorted(available_rooms, key=lambda r: r["capacity"])[0]["name"]
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room, d1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room, d2))
                        used_rooms[d1].append(room)
                        used_rooms[d2].append(room)
                        team_to_days[team_name] = [d1, d2]
                    else:
                        remaining.append((team_name, team_size, _))
                return remaining

            unplaced_teams += assign_combo(mon_wed, "Monday", "Wednesday")
            unplaced_teams += assign_combo(tue_thu, "Tuesday", "Thursday")

            for team_name, team_size, _ in unplaced_teams:
                placed = False
                for d1_label, d2_label in combinations(day_mapping.keys(), 2):
                    d1 = day_mapping[d1_label]
                    d2 = day_mapping[d2_label]
                    available_rooms = [
                        r for r in project_rooms
                        if r["name"] not in used_rooms[d1]
                        and r["name"] not in used_rooms[d2]
                        and r["capacity"] >= team_size
                    ]
                    if available_rooms:
                        room = sorted(available_rooms, key=lambda r: r["capacity"])[0]["name"]
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room, d1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)", (team_name, room, d2))
                        used_rooms[d1].append(room)
                        used_rooms[d2].append(room)
                        team_to_days[team_name] = [d1, d2]
                        placed = True
                        break
                if not placed:
                    print(f"❌ Could not place team: {team_name}")

        # --- Oasis Allocation ---
        if only in [None, "oasis"]:
            cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5 FROM oasis_preferences")
            person_rows = cur.fetchall()

            if not person_rows:
                conn.rollback()
                cur.close()
                conn.close()
                return False, ["No Oasis preferences found"]

            random.shuffle(person_rows)
            oasis_used = {d: set() for d in day_mapping.values()}
            person_to_days = {}
            person_prefs = {
                name: [d for d in [d1, d2, d3, d4, d5] if d and d in day_mapping]
                for name, d1, d2, d3, d4, d5 in person_rows
            }

            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name, date))
                        oasis_used[date].add(name)
                        person_to_days[name] = [date]
                        break

            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if name not in oasis_used[date] and len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)", (name, date))
                        oasis_used[date].add(name)
                        person_to_days.setdefault(name, []).append(date)

            if not any(oasis_used.values()):
                conn.rollback()
                cur.close()
                conn.close()
                return False, ["No Oasis allocations could be made."]

        conn.commit()
        cur.close()
        conn.close()
        return True, []

    except Exception as e:
        print(f"Allocation failed: {e}")
        return False, [str(e)]
