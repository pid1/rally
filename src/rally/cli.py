"""Rally CLI commands."""

from datetime import datetime

from rally.database import SessionLocal, init_db
from rally.models import DashboardSnapshot, Todo


def seed():
    """Seed the database with sample data for development."""
    init_db()
    db = SessionLocal()

    try:
        # Clear existing data
        db.query(DashboardSnapshot).delete()
        db.query(Todo).delete()
        db.commit()

        # Create sample dashboard snapshot
        today = datetime.now().strftime("%Y-%m-%d")
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
            "heads_up": "Don't forget: early release today at 2:00 PM. Also, soccer practice equipment needs to be packed before lunch.",
        }

        snapshot = DashboardSnapshot(date=today, data=sample_data, is_active=True)
        db.add(snapshot)

        # Create sample todos
        todos = [
            Todo(
                title="Schedule dentist appointments",
                description="Need to book checkups for the whole family",
                priority=8,
                completed=False,
            ),
            Todo(
                title="Plan weekend hike",
                description="Research trails and check weather forecast",
                priority=5,
                completed=False,
            ),
            Todo(
                title="Return library books",
                description="Due this Friday - in the bag by the door",
                priority=7,
                completed=False,
            ),
            Todo(
                title="Review budget spreadsheet",
                description="Monthly review of spending and savings goals",
                priority=6,
                completed=False,
            ),
            Todo(
                title="Call mom",
                description="Haven't talked in a while - give her a call this week",
                priority=4,
                completed=False,
            ),
            Todo(
                title="Finish reading chapter 3",
                description="Book club meets next week",
                priority=3,
                completed=False,
            ),
        ]

        for todo in todos:
            db.add(todo)

        db.commit()
        print("✅ Database seeded with sample data")
        print(f"   - 1 dashboard snapshot for {today}")
        print(f"   - {len(todos)} sample todos")

    except Exception as e:
        db.rollback()
        print(f"❌ Error seeding database: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
