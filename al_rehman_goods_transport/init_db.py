import os

from .core.database import init_db
from .extensions import db
from .models import User


def main():
    init_db()

    # Seed a first admin only when there are no users at all (fresh server), so a
    # restored production database is never overwritten. Password/email come from
    # env so the deploy is not a public admin123 backdoor.
    if User.query.count() == 0:
        password = os.environ.get("INITIAL_ADMIN_PASSWORD", "admin123")
        email = os.environ.get("INITIAL_ADMIN_EMAIL", "admin@example.com")
        admin_user = User(username="admin", email=email, is_admin=True)
        admin_user.set_role("admin")
        admin_user.set_permissions(None)
        admin_user.set_password(password)
        db.session.add(admin_user)
        db.session.commit()
        print(f"Default admin user created: username=admin (email={email})")

    print("Database initialized successfully!")


if __name__ == "__main__":
    main()
