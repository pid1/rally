"""Rally CLI commands."""

from rally.database import SessionLocal, init_db
from rally.models import Calendar, DashboardSnapshot, FamilyMember, Setting, Todo
from rally.utils.timezone import today_utc


def seed():
    """Seed the database with sample data for development."""
    init_db()
    db = SessionLocal()

    try:
        # Clear existing data
        db.query(Calendar).delete()
        db.query(Setting).delete()
        db.query(DashboardSnapshot).delete()
        db.query(Todo).delete()
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
        mom = FamilyMember(name="Mom", color="#4a6741")
        dad = FamilyMember(name="Dad", color="#5b4a8a")
        emma = FamilyMember(name="Emma", color="#8a4a5b")
        jake = FamilyMember(name="Jake", color="#4a708a")
        for member in [mom, dad, emma, jake]:
            db.add(member)
        db.flush()  # Get IDs assigned

        # Create sample calendars linked to family members
        calendars = [
            Calendar(label="Google Family", url="https://calendar.google.com/calendar/ical/example/basic.ics", family_member_id=mom.id),
            Calendar(label="iCloud Dad", url="https://p01-caldav.icloud.com/published/2/example", family_member_id=dad.id),
            Calendar(label="School Calendar", url="https://calendar.google.com/calendar/ical/school/basic.ics", family_member_id=emma.id),
        ]
        for cal in calendars:
            db.add(cal)

        # Create sample settings
        sample_settings = [
            Setting(key="local_timezone", value="America/Chicago"),
            Setting(key="weather_api_key", value="your_openweather_api_key_here"),
            Setting(key="weather_lat", value="32.7767"),
            Setting(key="weather_lon", value="-96.7970"),
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
                description="Need to book checkups for the whole family",
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

        db.commit()
        print("✅ Database seeded with sample data")
        print(f"   - 1 dashboard snapshot for {today}")
        print(f"   - 4 family members")
        print(f"   - {len(calendars)} calendars")
        print(f"   - {len(sample_settings)} settings")
        print(f"   - {len(todos)} sample todos")

    except Exception as e:
        db.rollback()
        print(f"❌ Error seeding database: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
