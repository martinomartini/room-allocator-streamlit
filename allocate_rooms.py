import psycopg2
import json
import os
from datetime import datetime, timedelta
import pytz
import random
from itertools import combinations

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
        else: # None or any other value means clear all
            cur.execute("DELETE FROM weekly_allocations")

        # --- Load Room Setup ---
        base_dir = os.path.dirname(os.path.abspath(__file__))
        rooms_file = os.path.join(base_dir, "rooms.json")
        with open(rooms_file, "r") as f:
            all_rooms_config = json.load(f)

        project_rooms = [r for r in all_rooms_config if r.get("name") != "Oasis"]
        oasis_config = next((r for r in all_rooms_config if r.get("name") == "Oasis"), None)
        if not oasis_config and only in [None, "oasis"]:
            print("Warning: Oasis room configuration not found in rooms.json")
            # Decide if this is fatal for oasis allocation or use a default
            oasis_config = {"name": "Oasis", "capacity": 15} # Default if not found


        # --- Project Room Allocation ---
        if only in [None, "project"]:
            cur.execute("SELECT team_name, team_size, preferred_days FROM weekly_preferences")
            team_preferences_raw = cur.fetchall()

            used_rooms_on_date = {date_obj: [] for date_obj in day_mapping.values()}
            placed_teams_info = {} # Stores {team_name: [date1, date2]}

            # Prepare team lists based on preferences
            mon_wed_teams = []
            tue_thu_teams = []
            # Store original preferred day labels for logging/debugging
            # Team tuple: (team_name, team_size, list_of_preferred_day_labels)

            for team_name, team_size, preferred_days_str in team_preferences_raw:
                pref_day_labels = sorted([d.strip() for d in preferred_days_str.split(',')])
                team_data = (team_name, team_size, pref_day_labels)
                if pref_day_labels == ["Monday", "Wednesday"]:
                    mon_wed_teams.append(team_data)
                elif pref_day_labels == ["Tuesday", "Thursday"]:
                    tue_thu_teams.append(team_data)
                else:
                    # For simplicity, teams with other/invalid preferences might be harder to place
                    # or could be added to a general pool later.
                    # For now, we'll assume preferences are validated upstream.
                    print(f"Team {team_name} has non-standard preference: {pref_day_labels}, will try fallback.")
                    # Add them to a list that goes directly to fallback, or handle as error
                    # For now, let's collect them for fallback.
                    mon_wed_teams.append(team_data) # Add to a default list if structure is unexpected


            # Shuffle initial order within preference groups for fairness before size sorting
            random.shuffle(mon_wed_teams)
            random.shuffle(tue_thu_teams)

            unplaced_after_preferred_pass = []

            def attempt_placement(teams_list, day1_label, day2_label):
                nonlocal used_rooms_on_date, placed_teams_info
                
                date1 = day_mapping[day1_label]
                date2 = day_mapping[day2_label]
                
                # Sort teams by size (descending) to give larger teams priority
                sorted_teams = sorted(teams_list, key=lambda x: x[1], reverse=True)
                
                still_unplaced = []

                for team_name, team_size, original_pref_labels in sorted_teams:
                    if team_name in placed_teams_info: continue # Already placed

                    possible_rooms = [
                        room for room in project_rooms
                        if room["name"] not in used_rooms_on_date[date1]
                        and room["name"] not in used_rooms_on_date[date2]
                        and room["capacity"] >= team_size
                    ]

                    if not possible_rooms:
                        still_unplaced.append((team_name, team_size, original_pref_labels))
                        continue

                    # Best-fit: find smallest capacity rooms that fit
                    min_suitable_capacity = float('inf')
                    for r in possible_rooms:
                        if r['capacity'] >= team_size:
                            min_suitable_capacity = min(min_suitable_capacity, r['capacity'])
                    
                    best_fit_rooms = [r for r in possible_rooms if r['capacity'] == min_suitable_capacity]
                    
                    if best_fit_rooms:
                        random.shuffle(best_fit_rooms) # Shuffle among best-fit rooms
                        chosen_room = best_fit_rooms[0]
                        
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room["name"], date1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room["name"], date2))
                        
                        used_rooms_on_date[date1].append(chosen_room["name"])
                        used_rooms_on_date[date2].append(chosen_room["name"])
                        placed_teams_info[team_name] = [date1, date2]
                    else:
                        # This case means min_suitable_capacity logic or filtering failed.
                        still_unplaced.append((team_name, team_size, original_pref_labels))
                
                return still_unplaced

            # --- Process preferred day pairs ---
            unplaced_after_preferred_pass.extend(attempt_placement(mon_wed_teams, "Monday", "Wednesday"))
            unplaced_after_preferred_pass.extend(attempt_placement(tue_thu_teams, "Tuesday", "Thursday"))
            
            random.shuffle(unplaced_after_preferred_pass) # Shuffle before fallback

            # --- Fallback for unplaced teams ---
            final_unplaced_teams = []
            # Sort by size for fallback as well
            sorted_unplaced_for_fallback = sorted(unplaced_after_preferred_pass, key=lambda x: x[1], reverse=True)

            for team_name, team_size, original_pref_labels in sorted_unplaced_for_fallback:
                if team_name in placed_teams_info: continue # Already placed

                placed_in_fallback = False
                possible_day_pairs = list(combinations(day_mapping.keys(), 2))
                random.shuffle(possible_day_pairs)

                for d1_label_fb, d2_label_fb in possible_day_pairs:
                    # Skip if this pair was their original preference and failed (already tried)
                    # This check is tricky if original_pref_labels wasn't strictly Mon/Wed or Tue/Thu
                    # For now, let's assume original_pref_labels accurately reflects their primary attempt.
                    # if sorted([d1_label_fb, d2_label_fb]) == original_pref_labels:
                    #     continue # Already tried this specific pair in the preferred pass

                    date1_fb = day_mapping[d1_label_fb]
                    date2_fb = day_mapping[d2_label_fb]

                    possible_rooms_fb = [
                        room for room in project_rooms
                        if room["name"] not in used_rooms_on_date[date1_fb]
                        and room["name"] not in used_rooms_on_date[date2_fb]
                        and room["capacity"] >= team_size
                    ]

                    if not possible_rooms_fb:
                        continue

                    min_suitable_capacity_fb = float('inf')
                    for r_fb in possible_rooms_fb:
                        if r_fb['capacity'] >= team_size:
                             min_suitable_capacity_fb = min(min_suitable_capacity_fb, r_fb['capacity'])
                    
                    best_fit_rooms_fb = [r_fb for r_fb in possible_rooms_fb if r_fb['capacity'] == min_suitable_capacity_fb]

                    if best_fit_rooms_fb:
                        random.shuffle(best_fit_rooms_fb)
                        chosen_room_fb = best_fit_rooms_fb[0]

                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_fb["name"], date1_fb))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room_fb["name"], date2_fb))
                        
                        used_rooms_on_date[date1_fb].append(chosen_room_fb["name"])
                        used_rooms_on_date[date2_fb].append(chosen_room_fb["name"])
                        placed_teams_info[team_name] = [date1_fb, date2_fb]
                        placed_in_fallback = True
                        break # Team placed, move to next unplaced team
                
                if not placed_in_fallback:
                    final_unplaced_teams.append((team_name, team_size, original_pref_labels))
                    print(f"❌ Fallback failed: Could not place team: {team_name} (Size: {team_size}, Pref: {original_pref_labels})")
            
            if final_unplaced_teams:
                 print(f"--- Project Allocation: {len(final_unplaced_teams)} teams could not be placed ---")


        # --- Oasis Allocation ---
        if only in [None, "oasis"]:
            if not oasis_config:
                print("Error: Oasis configuration missing, cannot perform Oasis allocation.")
                # Optionally, set conn.rollback() here if this is a critical failure
            else:
                cur.execute("""
                    SELECT person_name, preferred_day_1, preferred_day_2, 
                           preferred_day_3, preferred_day_4, preferred_day_5
                    FROM oasis_preferences
                """)
                person_rows = cur.fetchall()

                if not person_rows:
                    print("No Oasis preferences found for allocation.")
                else:
                    random.shuffle(person_rows) # Shuffle order of people
                    oasis_allocations_on_date = {date_obj: set() for date_obj in day_mapping.values()}
                    # person_to_assigned_dates = {} # Not strictly needed if just inserting

                    for person_name, d1, d2, d3, d4, d5 in person_rows:
                        preferred_day_labels_person = [day_label for day_label in [d1,d2,d3,d4,d5] if day_label and day_label in day_mapping]
                        random.shuffle(preferred_day_labels_person) # Shuffle their preferred days

                        assigned_count_for_person = 0
                        max_oasis_days_per_person = 2 # Example: try to give up to 2 days if possible

                        for day_label in preferred_day_labels_person:
                            if assigned_count_for_person >= max_oasis_days_per_person:
                                break

                            target_date = day_mapping[day_label]
                            # Check if person is already assigned to this specific date (e.g. from a previous preference)
                            # This check is more relevant if a person could have multiple entries or complex preference logic
                            # For now, we assume one row per person in oasis_preferences.
                            
                            # Check Oasis capacity for that day
                            if len(oasis_allocations_on_date[target_date]) < oasis_config["capacity"]:
                                # Check if this person is already in Oasis on this day (shouldn't happen with current loop)
                                # if person_name not in oasis_allocations_on_date[target_date]: # Redundant if loop is per person
                                cur.execute("""
                                    INSERT INTO weekly_allocations (team_name, room_name, date)
                                    VALUES (%s, %s, %s)
                                """, (person_name, oasis_config["name"], target_date))
                                oasis_allocations_on_date[target_date].add(person_name)
                                assigned_count_for_person += 1
                                # print(f"Allocated {person_name} to Oasis on {day_label}")


        conn.commit()
        cur.close()
        conn.close()
        print("Allocation process completed.")
        return True, []

    except psycopg2.Error as db_err:
        print(f"Database error during allocation: {db_err}")
        if conn: conn.rollback()
        return False, [str(db_err)]
    except Exception as e:
        print(f"General error during allocation: {e}")
        if conn: conn.rollback() # Rollback on general errors too
        return False, [str(e)]
    finally:
        if conn:
            cur.close()
            conn.close()
