"""Rally CLI commands."""

from datetime import timedelta

from rally import packing_lists as packing_lists_logic
from rally.calendars import series_end_date
from rally.calendars.inputs import resolve_event_times
from rally.database import SessionLocal, init_db
from rally.models import (
    Calendar,
    DashboardSnapshot,
    Event,
    EventAttendee,
    FamilyMember,
    MealPlan,
    Note,
    PackingListBag,
    PackingListDay,
    PackingListDayCheck,
    PackingListDayItem,
    PackingListItemHistory,
    PackingListTemplate,
    PackingListTemplateItem,
    PackingListTemplateSchedule,
    PrepItem,
    PrepLocation,
    RecurringTodo,
    Setting,
    ShoppingItem,
    ShoppingItemHistory,
    ShoppingStore,
    Todo,
)
from rally.utils.timezone import now_utc, today_utc


def seed():
    """Seed the database with sample data for development."""
    init_db()
    db = SessionLocal()

    try:
        # Clear existing data
        db.query(MealPlan).delete()
        db.query(Note).delete()
        for model in (
            PackingListDayCheck,
            PackingListDayItem,
            PackingListDay,
            PackingListTemplateSchedule,
            PackingListTemplateItem,
            PackingListItemHistory,
            PackingListBag,
            PackingListTemplate,
        ):
            db.query(model).delete()
        db.query(EventAttendee).delete()
        db.query(Event).delete()
        db.query(Calendar).delete()
        db.query(Setting).delete()
        db.query(DashboardSnapshot).delete()
        db.query(Todo).delete()
        db.query(RecurringTodo).delete()
        db.query(ShoppingItem).delete()
        db.query(ShoppingItemHistory).delete()
        db.query(ShoppingStore).delete()
        db.query(PrepItem).delete()
        db.query(PrepLocation).delete()
        db.query(FamilyMember).delete()
        db.commit()

        # Create sample dashboard snapshot
        today = today_utc().strftime("%Y-%m-%d")
        sample_data = {
            "greeting": "Good morning, family! It's a beautiful day to get things done.",
            "weather_summary": "Partly cloudy with highs around 68°F. Light jacket recommended for morning activities, but you can shed it by afternoon. No rain expected today.",
            "schedule": [
                {
                    "time": "7:30 AM",
                    "title": "Breakfast Together",
                    "notes": "Quick meal before everyone heads out",
                },
                {
                    "time": "9:00 AM",
                    "title": "School Drop-off",
                    "notes": "Kids have early release today - pickup at 2:00 PM instead of 3:00 PM",
                },
                {
                    "time": "10:00 AM - 12:00 PM",
                    "title": "Free Time",
                    "notes": "Good opportunity to tackle some high-priority todos",
                },
                {
                    "time": "12:30 PM",
                    "title": "Lunch with Sarah",
                    "notes": "Cafe on Main Street - she mentioned wanting to discuss summer plans",
                },
                {
                    "time": "2:00 PM",
                    "title": "School Pickup",
                    "notes": "Remember - early release today!",
                },
                {
                    "time": "3:00 PM",
                    "title": "Soccer Practice (Kids)",
                    "notes": "At the community field - practice runs until 4:30 PM",
                },
                {
                    "time": "5:30 PM",
                    "title": "Family Dinner",
                    "notes": "Taco Tuesday! Everyone's favorite.",
                },
                {
                    "time": "7:00 PM",
                    "title": "Homework Time",
                    "notes": "Kids have a math worksheet and reading assignment",
                },
            ],
            "briefing": "Don't forget: early release today at 2:00 PM. Also, soccer practice equipment needs to be packed before lunch.",
        }

        snapshot = DashboardSnapshot(date=today, data=sample_data, is_active=True)
        db.add(snapshot)

        # Create sample family members
        # Palette entries, not free hex: the seed has to produce rows the API
        # would accept, or a fresh `demo` instance is born failing validation.
        mom = FamilyMember(name="Mom", color="#3b8c61")
        dad = FamilyMember(name="Dad", color="#8859b1")
        emma = FamilyMember(name="Emma", color="#af2c3d")
        jake = FamilyMember(name="Jake", color="#315277")
        for member in [mom, dad, emma, jake]:
            db.add(member)
        db.flush()  # Get IDs assigned

        # No sample *external* calendars. The three that used to be seeded
        # pointed at `example` URLs that can never resolve, so every seeded
        # install rendered a permanent "Could not reach: Google Family, iCloud
        # Dad, School Calendar" banner across the calendar page — sample data
        # that demonstrates a failure. Connecting a real feed is a Settings
        # task with credentials behind it, not something a seed can fake.

        # Every family member gets a Rally-owned calendar to put events on.
        native_calendars = {
            member.id: Calendar(
                label=f"{member.name}'s Calendar",
                url="",
                family_member_id=member.id,
                cal_type="native",
            )
            for member in (mom, dad, emma, jake)
        }
        for cal in native_calendars.values():
            db.add(cal)
        db.commit()

        # Create sample events, including a recurring one, an all-day one and a
        # multi-day span, so /calendar has something honest to render.
        seed_tz = "America/Chicago"
        monday = today_utc() - timedelta(days=today_utc().weekday())
        event_specs = [
            (mom, "Scouts", 1, "19:00", 1, "FREQ=WEEKLY;BYDAY=TU", "Church hall", [mom, jake], 60),
            (dad, "Dentist — Emma", 2, "09:00", 1, None, "Dr. Kim", [dad, emma], 30),
            (emma, "Piano lesson", 3, "16:00", 1, None, "Studio B", [emma], None),
            (jake, "Soccer practice", 4, "17:30", 2, None, "Field 4", [jake, dad], None),
            (dad, "Camping trip", 5, None, 3, None, "Beavers Bend", [mom, dad, emma, jake], None),
        ]

        events = []
        for (
            owner,
            title,
            offset,
            start_time,
            span,
            rrule,
            location,
            attendees,
            notify,
        ) in event_specs:
            day = monday + timedelta(days=offset)
            if start_time is None:
                times = resolve_event_times(
                    start=day.isoformat(),
                    end=(day + timedelta(days=span - 1)).isoformat(),
                    all_day=True,
                    tzid=seed_tz,
                )
            else:
                start = f"{day.isoformat()}T{start_time}"
                hour, minute = (int(part) for part in start_time.split(":"))
                end_hour = hour + span
                times = resolve_event_times(
                    start=start,
                    end=f"{day.isoformat()}T{end_hour:02d}:{minute:02d}",
                    all_day=False,
                    tzid=seed_tz,
                )
            event = Event(
                calendar_id=native_calendars[owner.id].id,
                uid=f"rally-seed-{title.lower().replace(' ', '-')}@rally.local",
                title=title,
                location=location,
                rrule=rrule,
                series_end_date=series_end_date(rrule),
                notify_minutes_before=notify,
                **times,
            )
            db.add(event)
            events.append((event, attendees))
        db.commit()

        for event, attendees in events:
            for member in attendees:
                db.add(EventAttendee(event_id=event.id, family_member_id=member.id))

        # Create sample settings
        sample_settings = [
            Setting(key="local_timezone", value="America/Chicago"),
            Setting(
                key="weather_nws_url",
                value="https://forecast.weather.gov/MapClick.php?lat=33.085&lon=-97.0542&unit=0&lg=english&FcstType=dwml",
            ),
            Setting(key="llm_provider", value="local"),
            Setting(key="llm_local_base_url", value="http://localhost:1234/v1"),
            Setting(key="llm_local_model", value="your-model-name"),
        ]
        for s in sample_settings:
            db.add(s)

        # Create sample todos (some assigned, some family-wide)
        todos = [
            Todo(
                title="Schedule dentist appointments",
                description="Need to book checkups for the whole family. Dr. Ruiz: 206-555-0147",
                completed=False,
            ),
            Todo(
                title="Plan weekend hike",
                description="Research trails and check weather forecast",
                assigned_to=dad.id,
                completed=False,
            ),
            Todo(
                title="Return library books",
                description="Due this Friday - in the bag by the door",
                assigned_to=emma.id,
                completed=False,
            ),
            Todo(
                title="Review budget spreadsheet",
                description="Monthly review of spending and savings goals",
                assigned_to=mom.id,
                completed=False,
            ),
            Todo(
                title="Call mom",
                description="Haven't talked in a while - give her a call this week",
                assigned_to=dad.id,
                completed=False,
            ),
            Todo(
                title="Finish reading chapter 3",
                description="Book club meets next week",
                assigned_to=jake.id,
                completed=False,
            ),
        ]

        for todo in todos:
            db.add(todo)

        # Recurring templates, so /todo's Recurring section is not an empty
        # state on a fresh install. `last_generated_date` is left unset: the
        # first page load generates today's instances, which is the behavior
        # worth seeing rather than a row that looks inert.
        recurring_todos = [
            RecurringTodo(
                title="Take the bins out",
                description="Kerbside collection is Thursday morning",
                recurrence_type="weekly",
                recurrence_day=2,  # Wednesday, the night before
                assigned_to=jake.id,
                has_due_date=True,
            ),
            RecurringTodo(
                title="Change the furnace filter",
                description="20x25x1, spares are on the garage shelf",
                recurrence_type="monthly",
                recurrence_day=1,
                assigned_to=dad.id,
                has_due_date=True,
                remind_days_before=3,
            ),
            RecurringTodo(
                title="Water the plants",
                recurrence_type="daily",
                active=False,  # Deactivated rather than deleted, which is the point of the flag
            ),
        ]
        for recurring in recurring_todos:
            db.add(recurring)

        # Create a sample shopping list: two named stores plus catch-all items,
        # one already purchased today so the dimmed-until-midnight styling shows.
        costco = ShoppingStore(name="Costco")
        trader_joes = ShoppingStore(name="Trader Joe's")
        db.add_all([costco, trader_joes])
        db.flush()

        # `sort_order` is explicit because it is per-store and every row here
        # shares a created_at to the second: without it the seeded list would
        # have no defined order to demonstrate rearranging.
        shopping_items = [
            ShoppingItem(name="Paper towels", store_id=costco.id, sort_order=0),
            ShoppingItem(
                name="Rotisserie chicken",
                note="2 if they have them",
                store_id=costco.id,
                sort_order=1,
            ),
            ShoppingItem(
                name="Coffee beans",
                store_id=costco.id,
                completed=True,
                completed_at=now_utc(),
                sort_order=2,
            ),
            ShoppingItem(name="Almond milk", store_id=trader_joes.id, sort_order=0),
            ShoppingItem(name="Frozen dumplings", store_id=trader_joes.id, sort_order=1),
            ShoppingItem(name="Stamps", sort_order=0),
            ShoppingItem(name="Batteries", note="AA", sort_order=1),
        ]
        for item in shopping_items:
            db.add(item)

        # Seed the autocomplete vocabulary so suggestions have something to rank.
        history = [
            ("Milk", trader_joes.id, 24),
            ("Paper towels", costco.id, 12),
            ("Almond milk", trader_joes.id, 9),
            ("Coffee beans", costco.id, 7),
            ("Eggs", trader_joes.id, 6),
            ("Stamps", None, 2),
        ]
        for name, store_id, times_added in history:
            db.add(
                ShoppingItemHistory(
                    name_key=name.strip().casefold(),
                    name=name,
                    store_id=store_id,
                    times_added=times_added,
                )
            )

        # Create sample meal plans (multiple per date to showcase the feature)
        today_date = today_utc()

        # Preparedness stock. Locations carry an explicit `sort_order` because
        # a go list is walked in physical order — the truck on the driveway,
        # then the garage, then the basement — and the dates below are relative
        # to today so the page shows an overdue item, one inside its reminder
        # window and several simply scheduled, rather than one flat state.
        def in_days(days: int) -> str:
            return (today_date + timedelta(days=days)).strftime("%Y-%m-%d")

        truck, garage, basement = (
            PrepLocation(name="Truck", sort_order=1),
            PrepLocation(name="Garage shelf", sort_order=2),
            PrepLocation(name="Basement", sort_order=3),
        )
        db.add_all([truck, garage, basement])
        db.flush()

        prep_items = [
            PrepItem(
                name="Bottled water",
                quantity="6 cases",
                location_id=basement.id,
                notes="Rotate through the kitchen so nothing is ever thrown away",
                refresh_mode="interval",
                refresh_interval_months=6,
                next_refresh_date=in_days(-9),  # Overdue, and says so on the page
                last_refreshed_on=in_days(-192),
            ),
            PrepItem(
                name="First-aid kit",
                quantity="1",
                location_id=truck.id,
                notes="Check the burn gel and the children's paracetamol",
                refresh_mode="interval",
                refresh_interval_months=12,
                next_refresh_date=in_days(6),  # Inside the reminder window
                remind_days_before=14,
                last_refreshed_on=in_days(-359),
            ),
            PrepItem(
                name="Canned food",
                quantity="~40 tins",
                location_id=basement.id,
                notes="Stamped 2027-01-01; soup, beans, tuna",
                refresh_mode="date",
                next_refresh_date=in_days(45),
            ),
            PrepItem(
                name="Propane",
                quantity="2 tanks",
                location_id=garage.id,
                refresh_mode="interval",
                refresh_interval_months=3,
                next_refresh_date=in_days(21),
                last_refreshed_on=in_days(-70),
            ),
            PrepItem(
                name="Hand-crank radio",
                quantity="1",
                location_id=garage.id,
                notes="NOAA weather band; crank it once a season so the cell stays healthy",
                refresh_mode="none",
            ),
            PrepItem(
                name="Batteries",
                quantity="AA ×24, AAA ×12, D ×8",
                location_id=garage.id,
                refresh_mode="interval",
                refresh_interval_months=12,
                next_refresh_date=in_days(120),
                last_refreshed_on=in_days(-245),
            ),
            PrepItem(
                name="Wool blankets",
                quantity="4",
                location_id=truck.id,
                refresh_mode="none",
            ),
            PrepItem(
                name="Spare phone cable",
                quantity="2",
                # No location: the "Unassigned" group is a real state, and the
                # go list puts it last.
                refresh_mode="none",
            ),
        ]
        for prep_item in prep_items:
            db.add(prep_item)

        # Past meals across all meal types with a range of ratings (and some
        # unrated) so the Previous Meals page and its meal-type/rating filters
        # have realistic data to act on.
        past_meals = [
            MealPlan(
                date=(today_date - timedelta(days=2)).strftime("%Y-%m-%d"),
                meal_type="Breakfast",
                plan="Veggie omelettes and toast",
                cook_id=mom.id,
                rating=5,
                review="Fluffy and filling — a keeper for weekend mornings.",
            ),
            MealPlan(
                date=(today_date - timedelta(days=3)).strftime("%Y-%m-%d"),
                meal_type="Lunch",
                plan="Turkey and avocado sandwiches",
                attendee_ids=[emma.id, jake.id],
                rating=3,
            ),
            MealPlan(
                date=(today_date - timedelta(days=4)).strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Meatloaf with mashed potatoes",
                cook_id=dad.id,
                rating=4,
                review="Comfort food done right.",
            ),
            MealPlan(
                date=(today_date - timedelta(days=5)).strftime("%Y-%m-%d"),
                meal_type="Snacks",
                plan="Fruit and cheese board",
                rating=2,
                review="Fine, but the crackers were stale.",
            ),
            MealPlan(
                date=(today_date - timedelta(days=6)).strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Taco night",
                cook_id=mom.id,
                rating=5,
                review="Everyone's favorite — always a hit.",
            ),
            MealPlan(
                date=(today_date - timedelta(days=7)).strftime("%Y-%m-%d"),
                meal_type="Breakfast",
                plan="Oatmeal with berries",
                # Not yet rated
            ),
            MealPlan(
                date=(today_date - timedelta(days=9)).strftime("%Y-%m-%d"),
                meal_type="Lunch",
                plan="Grilled cheese and tomato soup",
                rating=4,
            ),
            MealPlan(
                date=(today_date - timedelta(days=10)).strftime("%Y-%m-%d"),
                meal_type="Snacks",
                plan="Popcorn and smoothies",
                # Not yet rated
            ),
            MealPlan(
                date=(today_date - timedelta(days=12)).strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Roast chicken with vegetables",
                cook_id=dad.id,
                rating=5,
                review="Crispy skin, juicy inside. Restaurant quality.",
            ),
            MealPlan(
                date=(today_date - timedelta(days=14)).strftime("%Y-%m-%d"),
                meal_type="Breakfast",
                plan="French toast",
                attendee_ids=[emma.id, jake.id],
                rating=3,
            ),
        ]
        for dp in past_meals:
            db.add(dp)

        meal_plans = [
            # Today: breakfast and dinner for different groups
            MealPlan(
                date=today_date.strftime("%Y-%m-%d"),
                meal_type="Breakfast",
                plan="Pancakes and bacon",
                attendee_ids=[dad.id, jake.id, emma.id],
                cook_id=dad.id,
            ),
            MealPlan(
                date=today_date.strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Chicken pot pie",
                attendee_ids=[dad.id, jake.id],
                cook_id=dad.id,
            ),
            MealPlan(
                date=today_date.strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Texas Roadhouse",
                attendee_ids=[mom.id, emma.id],
            ),
            # Tomorrow: whole family dinner
            MealPlan(
                date=(today_date + timedelta(days=1)).strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Spaghetti and meatballs with garlic bread",
                cook_id=mom.id,
            ),
            # Day after: lunch and dinner
            MealPlan(
                date=(today_date + timedelta(days=3)).strftime("%Y-%m-%d"),
                meal_type="Lunch",
                plan="Leftovers",
            ),
            MealPlan(
                date=(today_date + timedelta(days=3)).strftime("%Y-%m-%d"),
                meal_type="Dinner",
                plan="Grilled burgers and corn on the cob",
                cook_id=dad.id,
            ),
        ]
        for dp in meal_plans:
            db.add(dp)

        # Daily Notes. Today and one upcoming day so the Notes page and the
        # dashboard card both have something in them, plus a couple of weeks of
        # past notes so Previous Notes is worth opening.
        #
        # The past notes deliberately do not share a vocabulary: searching
        # "soccer", "Emma", "dentist" or "garage" each has to return a
        # different, non-empty subset, which is what makes the search bar
        # testable against seeded data rather than only against a real family's.
        # Markdown is spread across them for the same reason — bold, italics and
        # both list kinds all appear somewhere.
        note_bodies = {
            0: (
                "Soccer practice at 5 — **pack the soccer bag before school**.\n"
                "\n"
                "- Cleats are in the garage\n"
                "- Shin guards are in the dryer\n"
                "- Water bottle"
            ),
            1: (
                "Late pickup — **Mom has a 4:30 call**, so Dad collects the kids.\n"
                "Jake has chess club until 4:15."
            ),
            2: "Early start — *leave by 7:15* for the dentist.\nJake needs his retainer.",
            3: (
                "Library books are due back.\n\n- Two on the hall table\n- One still in Emma's room"
            ),
            4: "Picture day. *Collared shirts*, and the order form goes in with Jake.",
            6: (
                "Grocery run before the game.\n"
                "Kickoff is at 1, so **leave the house by noon** at the latest."
            ),
            -1: (
                "Emma's science fair board is due Friday.\n"
                "The poster tubes are on the shelf in the **garage**."
            ),
            -2: "Half day at school — pickup moves to *12:30*, not 3:00.",
            -3: "Trash out tonight. Recycling is **every other week** — this week it goes.",
            -4: (
                "Jake's dentist appointment moved to next Tuesday.\n"
                "\n"
                "1. Call the office to confirm\n"
                "2. Ask about the retainer\n"
                "3. Put it on the shared calendar"
            ),
            -5: "Grandma arrives Saturday around noon. **Spare room needs sheets.**",
            -7: (
                "Soccer photos today — *clean uniform*, hair brushed.\n"
                "Order form and check go in Emma's backpack."
            ),
            -8: "Plumber coming between 8 and 10. Someone needs to be home.",
            -10: (
                "Field trip money is due.\n"
                "\n"
                "- Jake: $12 for the aquarium\n"
                "- Emma: $8 for the museum"
            ),
            -12: "Book fair all week. Emma wants the dragon one — **$15 cash**, not card.",
            -14: "Car is in the shop. Carpool with the Hendersons for school runs this week.",
        }
        notes = [
            Note(date=(today_date + timedelta(days=offset)).strftime("%Y-%m-%d"), body=body)
            for offset, body in note_bodies.items()
        ]
        for note in notes:
            db.add(note)

        # Packing Lists: reusable packing lists, put on days. Each item names its
        # owner (or None for Everyone) and its bag (or None for No bag), so
        # both views — who owns what, what goes in each bag — have something
        # to show: Swim at Nana's splits by child into one pool bag; the
        # backpacks are one bag per child; the beach day is the family's.
        # The first day added is the coming Saturday's swim.
        packing_list_specs = [
            (
                "Swim at Nana's",
                "Saturdays at Nana's pool, a couple of times a month.",
                1,
                [
                    ("Emma", "Pool bag", "Swimsuit", None),
                    ("Emma", "Pool bag", "Goggles", "The blue pair. The green ones leak."),
                    ("Emma", "Pool bag", "Towel", None),
                    ("Emma", None, "Hair ties", None),
                    ("Jake", "Pool bag", "Swimsuit", None),
                    ("Jake", "Pool bag", "Rash guard", None),
                    ("Jake", "Pool bag", "Towel", None),
                    ("Jake", "Car", "Water wings", None),
                    (None, "Pool bag", "Sunscreen", "SPF 50. The bottle is nearly empty."),
                    (None, "Car", "Snacks for the drive", None),
                    (None, "Car", "Water bottles", None),
                    (None, "Pool bag", "Change of clothes", "One for each kid"),
                    (None, "Pool bag", "Wet bag for suits", None),
                ],
            ),
            (
                "School backpack",
                None,
                1,
                [
                    ("Emma", "Emma's backpack", "Homework folder", None),
                    ("Emma", "Emma's backpack", "Library book", "Due back Friday"),
                    ("Emma", "Emma's backpack", "PE shoes", None),
                    ("Jake", "Jake's backpack", "Lunchbox", None),
                    ("Jake", "Jake's backpack", "Take-home folder", None),
                    ("Jake", None, "Jacket", None),
                ],
            ),
            (
                "Beach day",
                "Lake beach with the kids.",
                0,
                [
                    (None, "Beach tote", "Beach towels", None),
                    (None, "Beach tote", "Sunscreen", None),
                    ("Dad", None, "Umbrella", None),
                    (None, "Beach tote", "Sand toys", None),
                    ("Mom", "Cooler", "Cooler with lunch", "Ice packs are in the garage freezer"),
                ],
            ),
            (
                "Dad's work bag",
                None,
                0,
                [
                    ("Dad", "Work bag", "Laptop and charger", None),
                    ("Dad", "Work bag", "Badge", None),
                    ("Dad", "Work bag", "Headphones", None),
                ],
            ),
        ]
        members_by_name = {m.name: m for m in (mom, dad, emma, jake)}
        bags: dict[str, PackingListBag] = {}
        packing_list_items: dict[tuple[str, str, str | None], PackingListTemplateItem] = {}
        seeded_packing_lists: dict[str, PackingListTemplate] = {}
        for name, description, timing, entries in packing_list_specs:
            packing_list = PackingListTemplate(
                name=name, description=description, pack_days_before=timing
            )
            db.add(packing_list)
            db.flush()
            seeded_packing_lists[name] = packing_list
            for position, (owner_name, bag_name, item_name, item_note) in enumerate(entries):
                if bag_name and bag_name not in bags:
                    bags[bag_name] = PackingListBag(name=bag_name)
                    db.add(bags[bag_name])
                    db.flush()
                owner = members_by_name[owner_name].id if owner_name else None
                bag = bags[bag_name].id if bag_name else None
                item = PackingListTemplateItem(
                    packing_list_template_id=packing_list.id,
                    owner_id=owner,
                    bag_id=bag,
                    name=item_name,
                    note=item_note,
                    sort_order=position,
                )
                db.add(item)
                db.flush()
                packing_lists_logic.record_item_history(db, item_name, owner, bag)
                db.flush()
                packing_list_items[(name, item_name, owner_name)] = item
        db.flush()

        # The coming Saturday — a week out when today already is one, so the
        # swim is always upcoming with a packing day before it.
        saturday = (5 - today_date.weekday()) % 7 or 7

        def put_on_day(name, offset, label=None, checked=()):
            day = PackingListDay(
                packing_list_template_id=seeded_packing_lists[name].id,
                date=in_days(offset),
                label=label,
            )
            db.add(day)
            db.flush()
            for item_name, owner_name in checked:
                item = packing_list_items[(name, item_name, owner_name)]
                db.add(PackingListDayCheck(day_id=day.id, template_item_id=item.id))
            return day

        nana_items = [(i, o) for o, _, i, _ in packing_list_specs[0][3]]
        work_bag_items = [(i, o) for o, _, i, _ in packing_list_specs[3][3]]
        packing_list_days = [
            put_on_day(
                "Swim at Nana's",
                saturday,
                label="Cousins are coming too",
                checked=[("Swimsuit", "Emma"), ("Goggles", "Emma"), ("Towel", "Emma")],
            ),
            put_on_day("Beach day", 6),
            put_on_day("Swim at Nana's", saturday - 14, checked=nana_items),
            put_on_day("Swim at Nana's", saturday - 28, checked=nana_items),
            # The archive needs days with more than one packing list and a list
            # that was not finished: a beach trip the same day as a swim, left
            # half packed; the work bag beside the older swim; and the work bag
            # alone on a weekday, missing the headphones.
            put_on_day(
                "Beach day",
                saturday - 14,
                checked=[("Beach towels", None), ("Sunscreen", None)],
            ),
            put_on_day("Dad's work bag", saturday - 28, checked=work_bag_items),
            put_on_day(
                "Dad's work bag",
                saturday - 10,
                checked=[("Laptop and charger", "Dad"), ("Badge", "Dad")],
            ),
        ]

        # A one-off on the swim's Saturday: a list with no template behind it,
        # so the demo shows one beside a template's day (which carries ⧉).
        concert = PackingListDay(
            packing_list_template_id=None,
            name="Fall concert",
            date=in_days(saturday),
            label="Lawn seats",
            pack_days_before=1,
        )
        db.add(concert)
        db.flush()
        for position, (owner_name, item_name) in enumerate(
            (("Mom", "Tickets"), (None, "Ear plugs"), ("Dad", "Phone charger"), (None, "Ponchos"))
        ):
            owner = members_by_name[owner_name].id if owner_name else None
            db.add(
                PackingListDayItem(
                    day_id=concert.id,
                    owner_id=owner,
                    name=item_name,
                    sort_order=position,
                )
            )
            packing_lists_logic.record_item_history(db, item_name, owner, None)
        packing_list_days.append(concert)

        # The backpack goes every school day, so it is a schedule rather than
        # days added by hand. Its days appear the first time the Packing Lists
        # page is loaded, a week ahead.
        db.add(
            PackingListTemplateSchedule(
                packing_list_template_id=seeded_packing_lists["School backpack"].id,
                recurrence_type="custom",
                custom_rule={"freq": "weekly", "interval": 1, "weekdays": [0, 1, 2, 3, 4]},
            )
        )
        # Dad's work bag repeats every weekday too, as Daily with weekends
        # skipped rather than a custom weekly rule, so a school day shows two
        # templated lists sharing one day box, and the demo shows both ways of
        # saying "weekdays".
        db.add(
            PackingListTemplateSchedule(
                packing_list_template_id=seeded_packing_lists["Dad's work bag"].id,
                recurrence_type="daily",
                custom_rule={"weekdays_only": True},
            )
        )

        db.commit()
        print("✅ Database seeded with sample data")
        print(f"   - 1 dashboard snapshot for {today}")
        print("   - 4 family members")
        print(f"   - {len(native_calendars)} Rally calendars with {len(events)} events")
        print(f"   - {len(sample_settings)} settings")
        print(f"   - {len(todos)} sample todos")
        print(f"   - {len(recurring_todos)} recurring task templates")
        print(f"   - {len(notes)} daily notes")
        print(f"   - {len(shopping_items)} shopping items across 2 stores")
        print(f"   - {len(history)} shopping history entries")
        print(f"   - {len(meal_plans)} upcoming meal plans")
        print(f"   - {len(past_meals)} past meal plans")
        print(f"   - {len(prep_items)} preparedness items across 3 locations")
        print(
            f"   - {len(seeded_packing_lists)} packing lists with {len(packing_list_items)} items,"
            f" on {len(packing_list_days)} days (one a one-off), plus two weekday schedules"
        )

    except Exception as e:
        db.rollback()
        print(f"❌ Error seeding database: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
