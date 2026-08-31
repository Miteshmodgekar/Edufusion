"""
Generate a sample attendance Excel file for testing.
Run: python generate_sample_excel.py
Output: sample_attendance.xlsx
"""

import pandas as pd
import random
from datetime import date, timedelta

def generate_sample_attendance(
    num_students=20,
    num_classes=40,
    subject="Data Structures",
    semester=4,
    section="A",
    output_file="sample_attendance.xlsx"
):
    # Generate date columns (Mon–Sat only, skip Sundays)
    dates = []
    d = date(2024, 1, 8)   # Start Jan 8 2024 (Monday)
    while len(dates) < num_classes:
        if d.weekday() < 6:  # Mon–Sat
            dates.append(d.strftime("%d-%b"))
        d += timedelta(days=1)

    # Student data
    names = [
        "Arjun Sharma", "Priya Singh", "Rahul Verma", "Sneha Nair", "Vikram Das",
        "Ananya Roy", "Karthik M", "Divya Pillai", "Arun Kumar", "Meera Iyer",
        "Suresh P", "Lakshmi V", "Nikhil G", "Pooja M", "Ravi S",
        "Kavya R", "Ajith T", "Swathy K", "Pranav N", "Deepthi A",
    ][:num_students]

    rolls = [f"CS{semester}200{str(i+1).zfill(2)}" for i in range(num_students)]

    rows = []
    for i, (roll, name) in enumerate(zip(rolls, names)):
        # Some students have high attendance, some low
        base_pct = random.choice([0.95, 0.90, 0.85, 0.80, 0.75, 0.70, 0.65, 0.55])
        row = {"Roll No": roll, "Name": name}
        for date_col in dates:
            row[date_col] = "P" if random.random() < base_pct else "A"
        rows.append(row)

    df = pd.DataFrame(rows)

    # Save
    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=f"{subject} - Sem{semester}{section}")

    # Stats
    attended = df[dates].apply(lambda col: (col == "P").sum())
    print(f"[✓] Generated: {output_file}")
    print(f"    Students  : {num_students}")
    print(f"    Classes   : {num_classes}")
    print(f"    Subject   : {subject} — Sem {semester} Section {section}")
    print(f"\n    Sample attendance percentages:")
    for _, row in df.iterrows():
        p_count = sum(1 for d in dates if row[d] == "P")
        pct = round(p_count / num_classes * 100, 1)
        flag = " ⚠" if pct < 85 else ""
        print(f"      {row['Roll No']} {row['Name']:<20} {pct}%{flag}")


if __name__ == "__main__":
    generate_sample_attendance(
        num_students=15,
        num_classes=40,
        subject="Data Structures",
        semester=4,
        section="A",
        output_file="sample_attendance.xlsx"
    )
