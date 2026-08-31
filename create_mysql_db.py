"""
MySQL Database Setup Script
Run this ONCE before starting the app to create the database.

Usage:
    python create_mysql_db.py

Make sure MySQL is running and you have set your password below.
"""

import sys

# ── EDIT THESE TO MATCH YOUR MYSQL SETUP ─────────────────────────────────────
MYSQL_HOST     = "localhost"
MYSQL_PORT     = 3306
MYSQL_USER     = "root"
MYSQL_PASSWORD = "Mitesh23"          # ← Put your MySQL root password here
MYSQL_DB       = "cse_smart_db"
# ─────────────────────────────────────────────────────────────────────────────


def create_database():
    try:
        import pymysql
    except ImportError:
        print("ERROR: pymysql not installed.")
        print("Run: pip install pymysql")
        sys.exit(1)

    print("=" * 50)
    print("CSE Smart System — MySQL Database Setup")
    print("=" * 50)
    print(f"\nConnecting to MySQL at {MYSQL_HOST}:{MYSQL_PORT}...")

    try:
        # Connect WITHOUT specifying a database first
        conn = pymysql.connect(
            host     = MYSQL_HOST,
            port     = MYSQL_PORT,
            user     = MYSQL_USER,
            password = MYSQL_PASSWORD,
            charset  = "utf8mb4",
        )
        cursor = conn.cursor()

        # Create the database if it doesn't exist
        cursor.execute(
            f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` "
            f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
        )
        conn.commit()
        print(f"  Database '{MYSQL_DB}' created (or already exists).")

        # Verify
        cursor.execute("SHOW DATABASES;")
        dbs = [row[0] for row in cursor.fetchall()]
        if MYSQL_DB in dbs:
            print(f"  Verified: '{MYSQL_DB}' is ready.")
        else:
            print(f"  WARNING: Database not found after creation!")

        cursor.close()
        conn.close()

        print("\n  Now updating config.py with your credentials...")
        _update_config()

        print("\n" + "=" * 50)
        print("  Setup complete!")
        print(f"  Database: {MYSQL_DB}")
        print(f"  Host:     {MYSQL_HOST}:{MYSQL_PORT}")
        print(f"  User:     {MYSQL_USER}")
        print("\n  Now run: python app.py")
        print("=" * 50)

    except pymysql.err.OperationalError as e:
        print(f"\nERROR: Cannot connect to MySQL.")
        print(f"  {e}")
        print("\nMake sure:")
        print("  1. MySQL is running (check MySQL Workbench or Services)")
        print("  2. Your password is correct in this script")
        print("  3. Port 3306 is not blocked")
        sys.exit(1)


def _update_config():
    """Update config.py with the credentials from this script."""
    config_path = "config.py"
    try:
        content = open(config_path).read()
        content = content.replace(
            '"localhost"',          f'"{MYSQL_HOST}"',         1)
        content = content.replace(
            '"3306"',               f'"{MYSQL_PORT}"',          1)
        content = content.replace(
            '"root"',               f'"{MYSQL_USER}"',          1)
        content = content.replace(
            'MYSQL_PASSWORD", "")', f'MYSQL_PASSWORD", "{MYSQL_PASSWORD}")', 1)
        content = content.replace(
            '"cse_smart_db"',       f'"{MYSQL_DB}"',            1)
        open(config_path, "w").write(content)
        print("  config.py updated.")
    except Exception as e:
        print(f"  Could not update config.py automatically: {e}")
        print(f"  Please manually set MYSQL_PASSWORD = '{MYSQL_PASSWORD}' in config.py")


if __name__ == "__main__":
    # If password passed as argument: python create_mysql_db.py mypassword
    if len(sys.argv) > 1:
        MYSQL_PASSWORD = sys.argv[1]
    create_database()
