import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random
from itertools import combinations

OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")  # Or your specific office timezone

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

def run_allocation(database_url, only=None):
    day_mapping = get_day_mapping()
    conn = None
    cur = None
    unplaced_project_team_messages = []

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        if only == "project":
            cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
        elif only == "oasis":
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            if cur.fetchone()[0] == 0:
                print("No oasis preferences submitted. Skipping Oasis allocation.")
                return True, ["No oasis preferences to allocate, so no changes made."]
            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'")
        else:
            cur.execute("DELETE FROM weekly_allocations")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        rooms_file_path = os.path.join(base_dir, "rooms.json")
        try:
            with open(rooms_file_path, "r") as f:
                all_rooms_config = json.load(f)
        except FileNotFoundError:
            return False, [f"CRITICAL ERROR: rooms.json not found at {rooms_file_path}"]
        except json.JSONDecodeError:
            return False, [f"CRITICAL ERROR: rooms.json at {rooms_file_path} is not valid JSON."]

        project_rooms = [r for r in all_rooms_config if r.get("name") != "Oasis" and "capacity" in r and "name" in r]
        oasis_config = next((r for r in all_rooms_config if r.get("name") == "Oasis" and "capacity" in r), None)

        if not project_rooms and only in [None, "project"]:
            print("Warning: No project rooms defined in rooms.json or they are malformed.")
        if not oasis_config and only in [None, "oasis"]:
            print("Warning: Oasis room configuration not found or malformed in rooms.json. Using default if needed.")
            oasis_config = {"name": "Oasis", "capacity": 15}

        if only in [None, "project"]:
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences_raw = cur.fetchall()

            used_rooms_on_date = {date_obj: [] for date_obj in day_mapping.values()}
            placed_teams_info = {}

            teams_for_mon_wed = []
            teams_for_tue_thu = []
            teams_for_fallback_immediately = []

            for team_name, team_size, preferred_days_str in team_preferences_raw:
                pref_day_labels = sorted([
                    day.strip().capitalize() for day in preferred_days_str.split(',') if day.strip()
                ])
                team_data = (team_name, int(team_size), pref_day_labels)

                if pref_day_labels == ["Monday", "Wednesday"]:
                    teams_for_mon_wed.append(team_data)
                elif pref_day_labels == ["Tuesday", "Thursday"]:
                    teams_for_tue_thu.append(team_data)
                else:
                    teams_for_fallback_immediately.append(team_data)

            random.shuffle(teams_for_mon_wed)
            random.shuffle(teams_for_tue_thu)
            random.shuffle(teams_for_fallback_immediately)

            def attempt_placement_for_pair(teams_list_for_pair, day1_label, day2_label):
                nonlocal used_rooms_on_date, placed_teams_info
                actual_date1 = day_mapping[day1_label]
                actual_date2 = day_mapping[day2_label]
                sorted_teams_for_pair = sorted(teams_list_for_pair, key=lambda x: x[1], reverse=True)
                still_unplaced_from_this_pair = []

                for team_name, team_size, original_pref_labels in sorted_teams_for_pair:
                    if team_name in placed_teams_info:
                        continue
                    possible_rooms_for_team = [
                        room_config for room_config in project_rooms
                        if room_config["name"] not in used_rooms_on_date[actual_date1]
                        and room_config["name"] not in used_rooms_on_date[actual_date2]
                        and room_config["capacity"] >= team_size
                    ]
                    if not possible_rooms_for_team:
                        still_unplaced_from_this_pair.append((team_name, team_size, original_pref_labels))
                        continue
                    min_suitable_capacity = min(r['capacity'] for r in possible_rooms_for_team)
                    best_fit_candidate_rooms = [r for r in possible_rooms_for_team if r['capacity'] == min_suitable_capacity]
                    random.shuffle(best_fit_candidate_rooms)
                    chosen_room_config = best_fit_candidate_rooms[0]
                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                (team_name, chosen_room_config["name"], actual_date1))
                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                (team_name, chosen_room_config["name"], actual_date2))
                    used_rooms_on_date[actual_date1].append(chosen_room_config["name"])
                    used_rooms_on_date[actual_date2].append(chosen_room_config["name"])
                    placed_teams_info[team_name] = [actual_date1, actual_date2]

                return still_unplaced_from_this_pair

            unplaced_after_mon_wed_pass = attempt_placement_for_pair(teams_for_mon_wed, "Monday", "Wednesday")
            unplaced_after_tue_thu_pass = attempt_placement_for_pair(teams_for_tue_thu, "Tuesday", "Thursday")
            master_fallback_pool = unplaced_after_mon_wed_pass + unplaced_after_tue_thu_pass + teams_for_fallback_immediately
            random.shuffle(master_fallback_pool)

            final_unplaced_project_teams = []
            sorted_fallback_teams = sorted(master_fallback_pool, key=lambda x: x[1], reverse=True)

            for team_name, team_size, original_pref_labels in sorted_fallback_teams:
                if team_name in placed_teams_info:
                    continue
                placed_in_fallback = False
                project_work_days = ["Monday", "Tuesday", "Wednesday", "Thursday"]
                possible_fallback_day_pairs = list(combinations(project_work_days, 2))
                random.shuffle(possible_fallback_day_pairs)

                for fb_day1_label, fb_day2_label in possible_fallback_day_pairs:
                    fb_actual_date1 = day_mapping[fb_day1_label]
                    fb_actual_date2 = day_mapping[fb_day2_label]
                    possible_rooms_for_fallback = [
                        room_config for room_config in project_rooms
                        if room_config["name"] not in used_rooms_on_date[fb_actual_date1]
                        and room_config["name"] not in used_rooms_on_date[fb_actual_date2]
                        and room_config["capacity"] >= team_size
                    ]
                    if not possible_rooms_for_fallback:
                        continue
                    min_suitable_capacity_fb = min(r['capacity'] for r in possible_rooms_for_fallback)
                    best_fit_candidate_rooms_fb = [r for r in possible_rooms_for_fallback if r['capacity'] == min_suitable_capacity_fb]
                    random.shuffle(best_fit_candidate_rooms_fb)
                    chosen_room_fb_config = best_fit_candidate_rooms_fb[0]
                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                (team_name, chosen_room_fb_config["name"], fb_actual_date1))
                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                (team_name, chosen_room_fb_config["name"], fb_actual_date2))
                    used_rooms_on_date[fb_actual_date1].append(chosen_room_fb_config["name"])
                    used_rooms_on_date[fb_actual_date2].append(chosen_room_fb_config["name"])
                    placed_teams_info[team_name] = [fb_actual_date1, fb_actual_date2]
                    placed_in_fallback = True
                    break

                if not placed_in_fallback:
                    final_unplaced_project_teams.append((team_name, team_size, original_pref_labels))

            if final_unplaced_project_teams:
                summary_message = f"--- Project Allocation: {len(final_unplaced_project_teams)} teams could not be placed. ---"
                print(summary_message)
                for team_name_unplaced, team_size_unplaced, original_pref_labels_unplaced in final_unplaced_project_teams:
                    msg = f"Unplaced Project Team: {team_name_unplaced} (Size: {team_size_unplaced}, Preferred Days: {original_pref_labels_unplaced})"
                    print(f"  {msg}")
                    unplaced_project_team_messages.append(msg)
            else:
                print("--- Project Allocation: All project teams were successfully placed. ---")

        if only in [None, "oasis"]:
            if not oasis_config:
                print("Error: Oasis configuration missing or malformed, cannot perform Oasis allocation.")
            else:
                cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5 FROM oasis_preferences")
                person_rows = cur.fetchall()
                if not person_rows:
                    print("No Oasis preferences found for allocation.")
                else:
                    oasis_allocations_on_actual_date = {date_obj: set() for date_obj in day_mapping.values()}
                    person_assigned_days = {row[0]: 0 for row in person_rows}
                    person_preferences = {}
                    day_to_people = {day: [] for day in day_mapping}

                    for person_name, d1, d2, d3, d4, d5 in person_rows:
                        prefs = [
                            day.strip().capitalize() for day in [d1, d2, d3, d4, d5]
                            if day and day.strip().capitalize() in day_mapping
                        ]
                        person_preferences[person_name] = prefs
                        for day in prefs:
                            day_to_people[day].append(person_name)

                    for day_label, date_obj in day_mapping.items():
                        candidates = [p for p in day_to_people[day_label] if person_assigned_days[p] == 0]
                        random.shuffle(candidates)
                        for person_name in candidates:
                            if len(oasis_allocations_on_actual_date[date_obj]) < oasis_config["capacity"]:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                            (person_name, oasis_config["name"], date_obj))
                                oasis_allocations_on_actual_date[date_obj].add(person_name)
                                person_assigned_days[person_name] += 1

                    still_assignable = True
                    while still_assignable:
                        still_assignable = False
                        all_people = list(person_preferences.keys())
                        random.shuffle(all_people)

                        for person_name in all_people:
                            if person_assigned_days[person_name] >= len(person_preferences[person_name]):
                                continue
                            for day_label in person_preferences[person_name]:
                                date_obj = day_mapping[day_label]
                                if person_name in oasis_allocations_on_actual_date[date_obj]:
                                    continue
                                if len(oasis_allocations_on_actual_date[date_obj]) < oasis_config["capacity"]:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                                (person_name, oasis_config["name"], date_obj))
                                    oasis_allocations_on_actual_date[date_obj].add(person_name)
                                    person_assigned_days[person_name] += 1
                                    still_assignable = True
                                    break

        conn.commit()
        return True, unplaced_project_team_messages

    except psycopg2.Error as db_err:
        error_msg = f"Database error during allocation: {db_err}"
        print(error_msg)
        if conn:
            conn.rollback()
        return False, [error_msg]
    except Exception as e:
        error_msg = f"General error during allocation: {type(e).__name__} - {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if conn:
            conn.rollback()
        return False, [error_msg]
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
