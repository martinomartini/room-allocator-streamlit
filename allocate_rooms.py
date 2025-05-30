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
        "Friday": (this_monday + timedelta(days=4)).date(), # Friday might be used for Oasis or other logic
    }

def run_allocation(database_url, only=None):
    """
    Runs the room allocation logic.
    'only' can be 'project', 'oasis', or None (for both).
    Returns (True, []) on success, (False, [error_messages]) on failure.
    """
    # print(f"--- Starting Allocation Run: only='{only}' ---")
    day_mapping = get_day_mapping()
    # print(f"Day mapping for this run: {day_mapping}")

    conn = None
    cur = None

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # --- Clear relevant previous allocations ---
        if only == "project":
            # print("Clearing previous PROJECT room allocations.")
            cur.execute("DELETE FROM weekly_allocations WHERE room_name != 'Oasis'")
        elif only == "oasis":
            cur.execute("SELECT COUNT(*) FROM oasis_preferences")
            if cur.fetchone()[0] == 0:
                print("No oasis preferences submitted. Skipping Oasis allocation.")
                # No need to rollback, just return as no operation was intended.
                return True, ["No oasis preferences to allocate, so no changes made."]
            # print("Clearing previous OASIS room allocations.")
            cur.execute("DELETE FROM weekly_allocations WHERE room_name = 'Oasis'")
        else: # None or any other value means clear all project and oasis allocations
            # print("Clearing ALL previous weekly_allocations.")
            cur.execute("DELETE FROM weekly_allocations")

        # --- Load Room Setup ---
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
            oasis_config = {"name": "Oasis", "capacity": 15} # Default if not found for Oasis allocation

        # --- Project Room Allocation ---
        if only in [None, "project"]:
            # print("--- Starting Project Room Allocation Phase ---")
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences_raw = cur.fetchall()
            # print(f"Fetched {len(team_preferences_raw)} project team preferences.")

            used_rooms_on_date = {date_obj: [] for date_obj in day_mapping.values()}
            placed_teams_info = {} # Stores {team_name: [actual_date1, actual_date2]}

            teams_for_mon_wed = []
            teams_for_tue_thu = []
            teams_for_fallback_immediately = []

            for team_name, team_size, preferred_days_str in team_preferences_raw:
                # Normalize preferred day labels: split, strip, capitalize
                pref_day_labels = sorted([
                    day.strip().capitalize() for day in preferred_days_str.split(',') if day.strip()
                ])
                team_data = (team_name, int(team_size), pref_day_labels) # Ensure team_size is int

                if pref_day_labels == ["Monday", "Wednesday"]:
                    teams_for_mon_wed.append(team_data)
                elif pref_day_labels == ["Tuesday", "Thursday"]:
                    teams_for_tue_thu.append(team_data)
                else:
                    # print(f"Team {team_name} (Size: {team_size}) has non-standard preference: {pref_day_labels}. Adding to direct fallback.")
                    teams_for_fallback_immediately.append(team_data)
            
            random.shuffle(teams_for_mon_wed)
            random.shuffle(teams_for_tue_thu)
            random.shuffle(teams_for_fallback_immediately)

            def attempt_placement_for_pair(teams_list_for_pair, day1_label, day2_label):
                nonlocal used_rooms_on_date, placed_teams_info # Allow modification of outer scope variables
                
                # print(f"Attempting placement for {len(teams_list_for_pair)} teams on {day1_label} & {day2_label}.")
                actual_date1 = day_mapping[day1_label]
                actual_date2 = day_mapping[day2_label]
                
                # Sort teams by size (descending) to give larger teams priority for better rooms
                sorted_teams_for_pair = sorted(teams_list_for_pair, key=lambda x: x[1], reverse=True)
                
                still_unplaced_from_this_pair = []

                for team_name, team_size, original_pref_labels in sorted_teams_for_pair:
                    if team_name in placed_teams_info: # Should not happen if lists are managed correctly
                        # print(f"Warning: Team {team_name} already in placed_teams_info, skipping in attempt_placement_for_pair.")
                        continue

                    # print(f"  Processing Team: {team_name} (Size: {team_size}) for {day1_label}/{day2_label}")
                    # print(f"    Rooms used on {day1_label} ({actual_date1}): {used_rooms_on_date[actual_date1]}")
                    # print(f"    Rooms used on {day2_label} ({actual_date2}): {used_rooms_on_date[actual_date2]}")

                    possible_rooms_for_team = [
                        room_config for room_config in project_rooms
                        if room_config["name"] not in used_rooms_on_date[actual_date1]
                        and room_config["name"] not in used_rooms_on_date[actual_date2]
                        and room_config["capacity"] >= team_size
                    ]
                    # print(f"    Found {len(possible_rooms_for_team)} possible rooms: {[r['name'] for r in possible_rooms_for_team]}")


                    if not possible_rooms_for_team:
                        # print(f"    No suitable and available rooms for {team_name} on {day1_label}/{day2_label}.")
                        still_unplaced_from_this_pair.append((team_name, team_size, original_pref_labels))
                        continue

                    # Best-fit: find smallest capacity rooms that still fit the team
                    min_suitable_capacity = min(r['capacity'] for r in possible_rooms_for_team) # Already filtered by >= team_size
                    
                    best_fit_candidate_rooms = [r for r in possible_rooms_for_team if r['capacity'] == min_suitable_capacity]
                    # print(f"    Best-fit capacity is {min_suitable_capacity}. Found {len(best_fit_candidate_rooms)} such rooms: {[r['name'] for r in best_fit_candidate_rooms]}")
                    
                    if best_fit_candidate_rooms:
                        random.shuffle(best_fit_candidate_rooms) # Shuffle among best-fit rooms for fairness
                        chosen_room_config = best_fit_candidate_rooms[0]
                        # print(f"    Placing {team_name} in {chosen_room_config['name']} on {day1_label}/{day2_label}.")
                        
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_config["name"], actual_date1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_config["name"], actual_date2))
                        
                        used_rooms_on_date[actual_date1].append(chosen_room_config["name"])
                        used_rooms_on_date[actual_date2].append(chosen_room_config["name"])
                        placed_teams_info[team_name] = [actual_date1, actual_date2]
                    else:
                        # This state should ideally not be reached if possible_rooms_for_team was not empty.
                        # print(f"    ERROR_UNEXPECTED: No best-fit rooms found for {team_name} on {day1_label}/{day2_label} despite having possible rooms. Adding to unplaced.")
                        still_unplaced_from_this_pair.append((team_name, team_size, original_pref_labels))
                
                return still_unplaced_from_this_pair

            # --- Process teams for their specific preferred day pairs ---
            unplaced_after_mon_wed_pass = attempt_placement_for_pair(teams_for_mon_wed, "Monday", "Wednesday")
            unplaced_after_tue_thu_pass = attempt_placement_for_pair(teams_for_tue_thu, "Tuesday", "Thursday")

            # Combine all teams that still need placement for the fallback phase
            master_fallback_pool = unplaced_after_mon_wed_pass + unplaced_after_tue_thu_pass + teams_for_fallback_immediately
            random.shuffle(master_fallback_pool) # Shuffle before sorting by size for fallback

            # print(f"--- Starting Fallback Project Room Allocation Phase for {len(master_fallback_pool)} teams ---")
            
            final_unplaced_project_teams = []
            # Sort by size for fallback as well, largest first
            sorted_fallback_teams = sorted(master_fallback_pool, key=lambda x: x[1], reverse=True)

            for team_name, team_size, original_pref_labels in sorted_fallback_teams:
                if team_name in placed_teams_info: # Should have been placed in preferred pass if possible
                    # print(f"Warning: Team {team_name} already in placed_teams_info, skipping in fallback.")
                    continue
                
                # print(f"  Fallback for Team: {team_name} (Size: {team_size}), Original Pref: {original_pref_labels}")
                placed_in_fallback = False
                
                # Generate all 2-day combinations from Monday-Thursday for fallback
                # Friday is typically not for 2-day project room bookings in this model
                project_work_days = ["Monday", "Tuesday", "Wednesday", "Thursday"]
                possible_fallback_day_pairs = list(combinations(project_work_days, 2))
                random.shuffle(possible_fallback_day_pairs) # Try day pairs in random order

                for fb_day1_label, fb_day2_label in possible_fallback_day_pairs:
                    # Optional: Don't re-try their original preferred pair if it already failed,
                    # unless it's the only option left or logic ensures it's a fresh attempt.
                    # if sorted([fb_day1_label, fb_day2_label]) == original_pref_labels:
                    #     # print(f"    Skipping fallback attempt on {fb_day1_label}/{fb_day2_label} as it was original preference.")
                    #     continue # This might be too restrictive if the reason for original failure was transient.

                    # print(f"    Trying fallback on {fb_day1_label} & {fb_day2_label}")
                    fb_actual_date1 = day_mapping[fb_day1_label]
                    fb_actual_date2 = day_mapping[fb_day2_label]

                    # print(f"      Rooms used on {fb_day1_label} ({fb_actual_date1}): {used_rooms_on_date[fb_actual_date1]}")
                    # print(f"      Rooms used on {fb_day2_label} ({fb_actual_date2}): {used_rooms_on_date[fb_actual_date2]}")

                    possible_rooms_for_fallback = [
                        room_config for room_config in project_rooms
                        if room_config["name"] not in used_rooms_on_date[fb_actual_date1]
                        and room_config["name"] not in used_rooms_on_date[fb_actual_date2]
                        and room_config["capacity"] >= team_size
                    ]
                    # print(f"      Found {len(possible_rooms_for_fallback)} possible rooms for fallback: {[r['name'] for r in possible_rooms_for_fallback]}")

                    if not possible_rooms_for_fallback:
                        continue # Try next day pair

                    min_suitable_capacity_fb = min(r['capacity'] for r in possible_rooms_for_fallback)
                    best_fit_candidate_rooms_fb = [r for r in possible_rooms_for_fallback if r['capacity'] == min_suitable_capacity_fb]
                    # print(f"      Best-fit capacity for fallback is {min_suitable_capacity_fb}. Found {len(best_fit_candidate_rooms_fb)} such rooms.")

                    if best_fit_candidate_rooms_fb:
                        random.shuffle(best_fit_candidate_rooms_fb)
                        chosen_room_fb_config = best_fit_candidate_rooms_fb[0]
                        # print(f"      Placing {team_name} in {chosen_room_fb_config['name']} on {fb_day1_label}/{fb_day2_label} (Fallback).")
                        
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_fb_config["name"], fb_actual_date1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_fb_config["name"], fb_actual_date2))
                        
                        used_rooms_on_date[fb_actual_date1].append(chosen_room_fb_config["name"])
                        used_rooms_on_date[fb_actual_date2].append(chosen_room_fb_config["name"])
                        placed_teams_info[team_name] = [fb_actual_date1, fb_actual_date2]
                        placed_in_fallback = True
                        break # Team placed, move to next team in fallback pool
                
                if not placed_in_fallback:
                    # print(f"    FAILURE: Could not place team {team_name} (Size: {team_size}) in fallback on any day pair.")
                    final_unplaced_project_teams.append((team_name, team_size, original_pref_labels))
            
            if final_unplaced_project_teams:
                 print(f"--- Project Allocation Complete: {len(final_unplaced_project_teams)} teams could not be placed. ---")
                 for team_info in final_unplaced_project_teams: print(f"  Unplaced: {team_info[0]}, Size: {team_info[1]}, Pref: {team_info[2]}")


        # --- Oasis Allocation ---
        if only in [None, "oasis"]:
            # print("--- Starting Oasis Allocation Phase ---")
            if not oasis_config:
                print("Error: Oasis configuration missing or malformed, cannot perform Oasis allocation.")
            else:
                cur.execute("""
                    SELECT person_name, preferred_day_1, preferred_day_2, 
                           preferred_day_3, preferred_day_4, preferred_day_5
                    FROM oasis_preferences
                """)
                person_rows = cur.fetchall()
                # print(f"Fetched {len(person_rows)} Oasis preferences.")

                if not person_rows:
                    print("No Oasis preferences found for allocation. No changes made to Oasis bookings.")
                else:
                    random.shuffle(person_rows) # Shuffle order of people for fairness
                    
                    # Use a fresh dict for Oasis, not related to project room usage
                    oasis_allocations_on_actual_date = {date_obj: set() for date_obj in day_mapping.values()}
                    
                    # Track how many days each person has been assigned to Oasis
                    person_assigned_oasis_days_count = {row[0]: 0 for row in person_rows}
                    max_oasis_days_per_person = 2 # Example: Aim to give up to 2 days if possible and capacity allows

                    for person_name, d1, d2, d3, d4, d5 in person_rows:
                        preferred_day_labels_for_person = [
                            day_label.strip().capitalize() for day_label in [d1,d2,d3,d4,d5] 
                            if day_label and day_label.strip().capitalize() in day_mapping
                        ]
                        random.shuffle(preferred_day_labels_for_person) # Shuffle their preferred days for random pick

                        # print(f"  Processing Oasis for: {person_name}, Shuffled Prefs: {preferred_day_labels_for_person}")

                        for day_label in preferred_day_labels_for_person:
                            if person_assigned_oasis_days_count[person_name] >= max_oasis_days_per_person:
                                # print(f"    {person_name} already assigned max ({max_oasis_days_per_person}) Oasis days.")
                                break # Person has reached their allocation limit

                            target_actual_date = day_mapping[day_label]
                            
                            # Check Oasis capacity for that day
                            if len(oasis_allocations_on_actual_date[target_actual_date]) < oasis_config["capacity"]:
                                # Check if this person is already in Oasis on this specific day (shouldn't be if logic is right)
                                if person_name not in oasis_allocations_on_actual_date[target_actual_date]:
                                    # print(f"    Allocating {person_name} to Oasis on {day_label} ({target_actual_date}). Capacity: {len(oasis_allocations_on_actual_date[target_actual_date])}/{oasis_config['capacity']}")
                                    cur.execute("""
                                        INSERT INTO weekly_allocations (team_name, room_name, date)
                                        VALUES (%s, %s, %s)
                                    """, (person_name, oasis_config["name"], target_actual_date))
                                    oasis_allocations_on_actual_date[target_actual_date].add(person_name)
                                    person_assigned_oasis_days_count[person_name] += 1
                                # else:
                                    # print(f"    {person_name} already in Oasis on {day_label} for this run, skipping duplicate add.")
                            # else:
                                # print(f"    Oasis full on {day_label} ({target_actual_date}). Capacity: {len(oasis_allocations_on_actual_date[target_actual_date])}/{oasis_config['capacity']}")
                    # print("--- Oasis Allocation Phase Complete ---")


        conn.commit()
        # print("--- Allocation Run Successfully Committed ---")
        return True, []

    except psycopg2.Error as db_err:
        error_msg = f"Database error during allocation: {db_err}"
        print(error_msg)
        if conn: conn.rollback()
        return False, [error_msg]
    except Exception as e:
        error_msg = f"General error during allocation: {type(e).__name__} - {e}"
        print(error_msg)
        import traceback
        traceback.print_exc() # Print full traceback for general errors
        if conn: conn.rollback()
        return False, [error_msg]
    finally:
        if cur:
            cur.close()
        if conn:
            conn.close()
        # print("--- Allocation Run Finished (Connection Closed) ---")
