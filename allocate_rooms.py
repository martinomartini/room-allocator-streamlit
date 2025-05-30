import random

# ... (existing imports and code above)

def run_allocation(database_url, only=None):
    day_mapping = get_day_mapping()

    try:
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        # --- Clear any previous allocations based on 'only' ---
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

            # Separate teams by preference
            for team_name, team_size, preferred_str in team_preferences:
                preferred_days = sorted([d.strip() for d in preferred_str.split(",") if d.strip() in day_mapping])
                if preferred_days == ["Monday", "Wednesday"]:
                    mon_wed.append((team_name, team_size, preferred_days))
                elif preferred_days == ["Tuesday", "Thursday"]:
                    tue_thu.append((team_name, team_size, preferred_days))
                else:
                    # Fallback if they picked something else
                    unplaced_teams.append((team_name, team_size, preferred_days))

            # Shuffle teams before assigning
            random.shuffle(mon_wed)
            random.shuffle(tue_thu)
            random.shuffle(unplaced_teams)

            def assign_combo(group, d1_label, d2_label):
                d1 = day_mapping[d1_label]
                d2 = day_mapping[d2_label]
                remaining = []

                for team_name, team_size, _ in group:
                    # Randomize the list of available rooms instead of taking the first
                    available_rooms = [
                        r for r in project_rooms
                        if r["name"] not in used_rooms[d1]
                        and r["name"] not in used_rooms[d2]
                        and r["capacity"] >= team_size
                    ]
                    random.shuffle(available_rooms)  # shuffle rooms for random assignment

                    if available_rooms:
                        chosen_room = available_rooms[0]["name"]
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room, d1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room, d2))
                        used_rooms[d1].append(chosen_room)
                        used_rooms[d2].append(chosen_room)
                        team_to_days[team_name] = [d1, d2]
                    else:
                        remaining.append((team_name, team_size, _))
                return remaining

            # Assign the mon_wed and tue_thu teams
            unplaced_teams += assign_combo(mon_wed, "Monday", "Wednesday")
            unplaced_teams += assign_combo(tue_thu, "Tuesday", "Thursday")

            # Try to place leftover teams for any two days
            leftover_shuffled = []
            for team_data in unplaced_teams:
                leftover_shuffled.append(team_data)
            random.shuffle(leftover_shuffled)

            for team_name, team_size, _ in leftover_shuffled:
                placed = False
                for (d1_label, d2_label) in combinations(day_mapping.keys(), 2):
                    d1 = day_mapping[d1_label]
                    d2 = day_mapping[d2_label]
                    available_rooms = [
                        r for r in project_rooms
                        if r["name"] not in used_rooms[d1]
                        and r["name"] not in used_rooms[d2]
                        and r["capacity"] >= team_size
                    ]
                    random.shuffle(available_rooms)
                    if available_rooms:
                        chosen_room = available_rooms[0]["name"]
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room, d1))
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, %s, %s)",
                                    (team_name, chosen_room, d2))
                        used_rooms[d1].append(chosen_room)
                        used_rooms[d2].append(chosen_room)
                        team_to_days[team_name] = [d1, d2]
                        placed = True
                        break
                if not placed:
                    print(f"❌ Could not place team: {team_name}")

        # --- Oasis Allocation (keep randomization as before) ---
        if only in [None, "oasis"]:
            cur.execute("""
                SELECT person_name, preferred_day_1, preferred_day_2, preferred_day_3, preferred_day_4, preferred_day_5
                FROM oasis_preferences
            """)
            person_rows = cur.fetchall()

            if not person_rows:
                conn.rollback()
                cur.close()
                conn.close()
                return False, ["No Oasis preferences found"]

            # Shuffle the order of people
            random.shuffle(person_rows)
            oasis_used = {d: set() for d in day_mapping.values()}
            person_to_days = {}
            person_prefs = {}

            # Build out each person's day preferences, and shuffle them too
            for (name, d1, d2, d3, d4, d5) in person_rows:
                prefs = [d for d in [d1, d2, d3, d4, d5] if d and d in day_mapping]
                random.shuffle(prefs)  # randomize each individual's preferred day order
                person_prefs[name] = prefs

            # Each person tries to get first available day from their shuffled prefs
            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)",
                                    (name, date))
                        oasis_used[date].add(name)
                        person_to_days[name] = [date]
                        break

            # Try to see if they can get more days from their list if capacity still available
            for name, prefs in person_prefs.items():
                for day in prefs:
                    date = day_mapping[day]
                    if name not in oasis_used[date] and len(oasis_used[date]) < oasis["capacity"]:
                        cur.execute("INSERT INTO weekly_allocations (team_name, room_name, date) VALUES (%s, 'Oasis', %s)",
                                    (name, date))
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