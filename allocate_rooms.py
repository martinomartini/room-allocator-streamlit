import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random
from itertools import combinations

OFFICE_TIMEZONE = pytz.timezone("Europe/Amsterdam")  # Or your specific office timezone

def get_day_mapping(base_monday_date=None):
    """
    Get day mapping for the week using the provided base_monday_date.
    NO automatic date calculation - base_monday_date is required.
    """
    if base_monday_date is None:
        raise ValueError("base_monday_date is required - no automatic date calculation allowed. This prevents unexpected week resets.")
    
    # Ensure the provided date is actually a Monday
    if base_monday_date.weekday() != 0:
        raise ValueError(f"base_monday_date must be a Monday. Provided date {base_monday_date} is a {base_monday_date.strftime('%A')}")
    
    this_monday = base_monday_date
    
    return {
        "Monday": this_monday,
        "Tuesday": this_monday + timedelta(days=1),
        "Wednesday": this_monday + timedelta(days=2),
        "Thursday": this_monday + timedelta(days=3),
        "Friday": this_monday + timedelta(days=4),
    }

def run_allocation(database_url, only=None, base_monday_date=None):
    """
    Run room allocation for a specific week.
    
    Args:
        database_url: Database connection string
        only: "project" or "oasis" to run only that allocation, None for both
        base_monday_date: REQUIRED - Static Monday date to use (date object). No automatic date calculation.
    
    Returns:
        tuple: (success: bool, messages: list)
    """
    if base_monday_date is None:
        error_msg = "CRITICAL ERROR: base_monday_date is required. No automatic date calculation allowed to prevent unexpected week resets."
        print(error_msg)
        return False, [error_msg]
    
    try:
        day_mapping = get_day_mapping(base_monday_date)
        print(f"Running allocation for week of {base_monday_date.strftime('%Y-%m-%d')} (Monday)")
        print(f"Day mapping: {day_mapping}")
    except ValueError as e:
        error_msg = f"Date validation error: {e}"
        print(error_msg)
        return False, [error_msg]
    
    conn = None
    cur = None
    unplaced_project_team_messages = []

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        if only == "project":
            # Only delete project allocations for the specific week
            cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis' AND date >= %s AND date <= %s", 
                       (base_monday_date, base_monday_date + timedelta(days=6)))
            print(f"Cleared project room allocations for week of {base_monday_date}")
        elif only == "oasis":
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            if cur.fetchone()[0] == 0:
                print("No oasis preferences submitted. Skipping Oasis allocation.")
                return True, ["No oasis preferences to allocate, so no changes made."]
            # Only delete Oasis allocations for the specific week
            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis' AND date >= %s AND date <= %s", 
                       (base_monday_date, base_monday_date + timedelta(days=6)))
            print(f"Cleared Oasis allocations for week of {base_monday_date}")
        else:
            # Delete all allocations for the specific week only
            cur.execute("DELETE FROM weekly_allocations WHERE date >= %s AND date <= %s", 
                       (base_monday_date, base_monday_date + timedelta(days=6)))
            print(f"Cleared all allocations for week of {base_monday_date}")

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
            print("Starting project room allocation...")
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences_raw = cur.fetchall()
            print(f"Found {len(team_preferences_raw)} team preferences")

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

            print(f"Teams preferring Mon/Wed: {len(teams_for_mon_wed)}")
            print(f"Teams preferring Tue/Thu: {len(teams_for_tue_thu)}")
            print(f"Teams with other preferences: {len(teams_for_fallback_immediately)}")

            random.shuffle(teams_for_mon_wed)
            random.shuffle(teams_for_tue_thu)
            random.shuffle(teams_for_fallback_immediately)

            def attempt_placement_for_pair(teams_list_for_pair, day1_label, day2_label):
                nonlocal used_rooms_on_date, placed_teams_info
                actual_date1 = day_mapping[day1_label]
                actual_date2 = day_mapping[day2_label]
                sorted_teams_for_pair = sorted(teams_list_for_pair, key=lambda x: x[1], reverse=True)
                still_unplaced_from_this_pair = []

                print(f"Attempting placement for {day1_label}/{day2_label} - {len(sorted_teams_for_pair)} teams")

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
                    print(f"Placed team {team_name} in {chosen_room_config['name']} for {day1_label}/{day2_label}")

                return still_unplaced_from_this_pair

            unplaced_after_mon_wed_pass = attempt_placement_for_pair(teams_for_mon_wed, "Monday", "Wednesday")
            unplaced_after_tue_thu_pass = attempt_placement_for_pair(teams_for_tue_thu, "Tuesday", "Thursday")
            master_fallback_pool = unplaced_after_mon_wed_pass + unplaced_after_tue_thu_pass + teams_for_fallback_immediately
            random.shuffle(master_fallback_pool)

            print(f"Fallback allocation needed for {len(master_fallback_pool)} teams")

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
                    print(f"Fallback: Placed team {team_name} in {chosen_room_fb_config['name']} for {fb_day1_label}/{fb_day2_label}")
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
            print("Starting Oasis allocation...")
            if not oasis_config:
                print("Error: Oasis configuration missing or malformed, cannot perform Oasis allocation.")
            else:
                cur.execute("SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5 FROM oasis_preferences")
                person_rows = cur.fetchall()
                print(f"Found {len(person_rows)} Oasis preferences")
                
                if not person_rows:
                    print("No Oasis preferences found for allocation.")
                else:
                    oasis_allocations_on_actual_date = {date_obj: set() for date_obj in day_mapping.values()}
                    person_assigned_days = {row[0]: 0 for row in person_rows}
                    person_preferences = {}
                    day_to_people = {day: [] for day in day_mapping}

                    # Parse preferences
                    for person_name, d1, d2, d3, d4, d5 in person_rows:
                        prefs = [
                            day.strip().capitalize() for day in [d1, d2, d3, d4, d5]
                            if day and day.strip().capitalize() in day_mapping
                        ]
                        person_preferences[person_name] = prefs
                        for day in prefs:
                            day_to_people[day].append(person_name)
                        print(f"Person {person_name} prefers: {prefs}")

                    # First pass: Give everyone at least one day (priority to those with 0 assignments)
                    for day_label, date_obj in day_mapping.items():
                        candidates = [p for p in day_to_people[day_label] if person_assigned_days[p] == 0]
                        random.shuffle(candidates)
                        for person_name in candidates:
                            if len(oasis_allocations_on_actual_date[date_obj]) < oasis_config["capacity"]:
                                cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                            (person_name, oasis_config["name"], date_obj))
                                oasis_allocations_on_actual_date[date_obj].add(person_name)
                                person_assigned_days[person_name] += 1
                                print(f"First pass: Assigned {person_name} to {day_label} ({date_obj})")

                    # Additional passes: Fill remaining spots
                    still_assignable = True
                    pass_number = 2
                    while still_assignable:
                        still_assignable = False
                        all_people = list(person_preferences.keys())
                        random.shuffle(all_people)

                        print(f"Oasis allocation pass {pass_number}")
                        for person_name in all_people:
                            if person_assigned_days[person_name] >= len(person_preferences[person_name]):
                                continue  # Person has been assigned to all their preferred days
                            
                            for day_label in person_preferences[person_name]:
                                date_obj = day_mapping[day_label]
                                if person_name in oasis_allocations_on_actual_date[date_obj]:
                                    continue  # Person already assigned to this day
                                
                                if len(oasis_allocations_on_actual_date[date_obj]) < oasis_config["capacity"]:
                                    cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                                (person_name, oasis_config["name"], date_obj))
                                    oasis_allocations_on_actual_date[date_obj].add(person_name)
                                    person_assigned_days[person_name] += 1
                                    still_assignable = True
                                    print(f"Pass {pass_number}: Assigned {person_name} to {day_label} ({date_obj})")
                                    break
                        pass_number += 1

                    # Print final Oasis summary
                    print("Final Oasis allocation summary:")
                    for day_label, date_obj in day_mapping.items():
                        assigned_count = len(oasis_allocations_on_actual_date[date_obj])
                        available_spots = oasis_config["capacity"] - assigned_count
                        print(f"  {day_label} ({date_obj}): {assigned_count}/{oasis_config['capacity']} assigned, {available_spots} spots available")

        conn.commit()
        print(f"Allocation completed successfully for week of {base_monday_date}")
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