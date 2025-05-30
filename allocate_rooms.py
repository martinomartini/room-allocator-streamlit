import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random
from itertools import combinations

OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam") # Or your specific office timezone

def get_day_mapping():
    """Gets a mapping of day names to datetime.date objects for the current week."""
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
    """
    Runs the room allocation logic.
    'only' can be 'project', 'oasis', or None (for both).
    Returns (True, list_of_unplaced_project_team_messages) on success,
            (False, [error_messages]) on failure.
    """
    # print(f"--- Starting Allocation Run: only='{only}' ---")
    day_mapping = get_day_mapping()
    conn = None
    cur = None
    unplaced_project_team_messages = [] # Initialize list to store messages about unplaced teams

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
            # print("--- Starting Project Room Allocation Phase ---")
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences_raw = cur.fetchall()
            # print(f"Fetched {len(team_preferences_raw)} project team preferences.")

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
                    if team_name in placed_teams_info: continue
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
                    if best_fit_candidate_rooms:
                        random.shuffle(best_fit_candidate_rooms)
                        chosen_room_config = best_fit_candidate_rooms[0]
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_config["name"], actual_date1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_config["name"], actual_date2))
                        used_rooms_on_date[actual_date1].append(chosen_room_config["name"])
                        used_rooms_on_date[actual_date2].append(chosen_room_config["name"])
                        placed_teams_info[team_name] = [actual_date1, actual_date2]
                    else:
                        still_unplaced_from_this_pair.append((team_name, team_size, original_pref_labels))
                return still_unplaced_from_this_pair

            unplaced_after_mon_wed_pass = attempt_placement_for_pair(teams_for_mon_wed, "Monday", "Wednesday")
            unplaced_after_tue_thu_pass = attempt_placement_for_pair(teams_for_tue_thu, "Tuesday", "Thursday")
            master_fallback_pool = unplaced_after_mon_wed_pass + unplaced_after_tue_thu_pass + teams_for_fallback_immediately
            random.shuffle(master_fallback_pool)
            
            final_unplaced_project_teams = []
            sorted_fallback_teams = sorted(master_fallback_pool, key=lambda x: x[1], reverse=True)

            for team_name, team_size, original_pref_labels in sorted_fallback_teams:
                if team_name in placed_teams_info: continue
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
                    if not possible_rooms_for_fallback: continue
                    min_suitable_capacity_fb = min(r['capacity'] for r in possible_rooms_for_fallback)
                    best_fit_candidate_rooms_fb = [r for r in possible_rooms_for_fallback if r['capacity'] == min_suitable_capacity_fb]
                    if best_fit_candidate_rooms_fb:
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
                # unplaced_project_team_messages.append(summary_message) # Optionally add summary to returned list
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
                    random.shuffle(person_rows)
                    oasis_allocations_on_actual_date = {date_obj: set() for date_obj in day_mapping.values()}
                    person_assigned_oasis_days_count = {row[0]: 0 for row in person_rows}
                    max_oasis_days_per_person = 2

                    for person_name, d1, d2, d3, d4, d5 in person_rows:
                        preferred_day_labels_for_person = [
                            day_label.strip().capitalize() for day_label in [d1,d2,d3,d4,d5] 
                            if day_label and day_label.strip().capitalize() in day_mapping
                        ]
                        random.shuffle(preferred_day_labels_for_person)
                        for day_label in preferred_day_labels_for_person:
                            if person_assigned_oasis_days_count[person_name] >= max_oasis_days_per_person:
                                break
                            target_actual_date = day_mapping[day_label]
                            if len(oasis_allocations_on_actual_date[target_actual_date]) < oasis_config["capacity"]:
                                if person_name not in oasis_allocations_on_actual_date[target_actual_date]:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                                (person_name, oasis_config["name"], target_actual_date))
                                    oasis_allocations_on_actual_date[target_actual_date].add(person_name)
                                    person_assigned_oasis_days_count[person_name] += 1
        conn.commit()
        return True, unplaced_project_team_messages

    except psycopg2.Error as db_err:
        error_msg = f"Database error during allocation: {db_err}"
        print(error_msg)
        if conn: conn.rollback()
        return False, [error_msg]
    except Exception as e:
        error_msg = f"General error during allocation: {type(e).__name__} - {e}"
        print(error_msg)
        import traceback
        traceback.print_exc()
        if conn: conn.rollback()
        return False, [error_msg]
    finally:
        if cur: cur.close()
        if conn: conn.close()
