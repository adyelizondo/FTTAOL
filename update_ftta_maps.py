#!/usr/bin/env python3
"""
FTTA Online Map Data Updater
=============================
Fetches the latest enrollment data from the FTTA Online feed,
processes it, and updates the HTML map files in this folder.

Double-click to launch — a friendly window will guide you through.

Requirements:
    Python 3.6+ (standard library only, no pip installs needed)
"""

import os
import re
import json
import ssl
import sys
import threading
import webbrowser
from datetime import datetime
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from collections import defaultdict

# ── Configuration ──────────────────────────────────────────────
FEED_URL = os.environ.get("FTTA_FEED_URL", "")  # set via GitHub Secret FTTA_FEED_URL
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── ISO 2-letter to 3-letter country code mapping ─────────────
ISO2_TO_ISO3 = {
    "US": "USA", "CA": "CAN", "GB": "GBR", "AU": "AUS", "NZ": "NZL",
    "SG": "SGP", "MY": "MYS", "PH": "PHL", "ZA": "ZAF", "GH": "GHA",
    "ET": "ETH", "KR": "KOR", "IL": "ISR", "BR": "BRA", "UA": "UKR",
    "HK": "HKG", "TW": "TWN", "JP": "JPN", "FR": "FRA", "GR": "GRC",
    "ID": "IDN", "CN": "CHN", "CO": "COL", "NO": "NOR", "VN": "VNM",
    "PK": "PAK", "HN": "HND", "RU": "RUS", "AT": "AUT", "BE": "BEL",
    "TH": "THA", "CL": "CHL", "EG": "EGY", "ES": "ESP", "MX": "MEX",
    "NG": "NGA", "IN": "IND", "DE": "DEU", "IE": "IRL", "KZ": "KAZ",
    "AE": "ARE", "BZ": "BLZ", "GU": "GUM", "BW": "BWA", "KE": "KEN",
    "NL": "NLD", "UG": "UGA", "AL": "ALB", "CH": "CHE", "CZ": "CZE",
    "IT": "ITA", "RO": "ROU", "ZW": "ZWE", "GE": "GEO", "VC": "VCT",
    "PR": "PRI", "UK": "GBR",
}

# ── ISO3 to country name mapping ──────────────────────────────
ISO3_TO_NAME = {
    "USA": "United States", "CAN": "Canada", "GBR": "United Kingdom",
    "AUS": "Australia", "NZL": "New Zealand", "SGP": "Singapore",
    "MYS": "Malaysia", "PHL": "Philippines", "ZAF": "South Africa",
    "GHA": "Ghana", "ETH": "Ethiopia", "KOR": "South Korea",
    "ISR": "Israel", "BRA": "Brazil", "UKR": "Ukraine", "HKG": "Hong Kong",
    "TWN": "Taiwan", "JPN": "Japan", "FRA": "France", "GRC": "Greece",
    "IDN": "Indonesia", "CHN": "China", "COL": "Colombia", "NOR": "Norway",
    "VNM": "Vietnam", "PAK": "Pakistan", "HND": "Honduras", "RUS": "Russia",
    "AUT": "Austria", "BEL": "Belgium", "THA": "Thailand", "CHL": "Chile",
    "EGY": "Egypt", "ESP": "Spain", "MEX": "Mexico", "NGA": "Nigeria",
    "IND": "India", "DEU": "Germany", "IRL": "Ireland", "KAZ": "Kazakhstan",
    "ARE": "United Arab Emirates", "BLZ": "Belize", "GUM": "Guam",
    "BWA": "Botswana", "KEN": "Kenya", "NLD": "Netherlands", "UGA": "Uganda",
    "ALB": "Albania", "CHE": "Switzerland", "CZE": "Czech Republic",
    "ITA": "Italy", "ROU": "Romania", "ZWE": "Zimbabwe", "GEO": "Georgia",
    "VCT": "St. Vincent", "PRI": "Puerto Rico",
}


# ═══════════════════════════════════════════════════════════════
#  Data processing functions
# ═══════════════════════════════════════════════════════════════

def fetch_data(log):
    """Download the raw CSV feed."""
    if not FEED_URL:
        raise RuntimeError("FTTA_FEED_URL is not set. Add it as a GitHub Secret.")
    log("Connecting to FTTA Online feed...")
    req = Request(FEED_URL, headers={"User-Agent": "FTTA-Map-Updater/1.0"})

    # Try default SSL first; fall back to unverified if certs aren't installed (common on Mac)
    try:
        resp_ctx = urlopen(req, timeout=30)
    except URLError as ssl_err:
        if "CERTIFICATE_VERIFY_FAILED" in str(ssl_err):
            log("  SSL certificate not found — using fallback (this is safe)")
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp_ctx = urlopen(req, timeout=30, context=ctx)
        else:
            raise

    with resp_ctx as resp:
        raw = resp.read().decode("utf-8", errors="replace")

    # The feed returns records separated by <br /> tags, not newlines
    raw = raw.replace('<br />', '\n').replace('<br/>', '\n').replace('<br>', '\n')
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    log(f"  Downloaded {len(lines):,} rows")
    return lines


def parse_rows(lines, log):
    """Parse CSV rows using regex to handle commas in city names."""
    course_re = re.compile(r',([A-Z]{2,4}\d{1,2}-(?:\d{2}[A-E]|FTT15)),')
    records = []
    skipped = 0

    for line in lines:
        m = course_re.search(line)
        if not m:
            skipped += 1
            continue

        course = m.group(1)
        before = line[:m.start()]
        after = line[m.end():]

        parts_before = before.rsplit(",", 1)
        if len(parts_before) != 2:
            skipped += 1
            continue
        eid, uid = parts_before

        country_raw = after[-2:].strip().upper()
        city = after[:-3].strip() if len(after) > 2 else ""

        year = extract_year(course)
        if year is None:
            skipped += 1
            continue

        records.append({
            "eid": eid.strip(), "uid": uid.strip(), "course": course,
            "city": city, "country_raw": country_raw, "year": year,
        })

    if skipped > 0:
        log(f"  Skipped {skipped} unparseable rows")
    log(f"  Parsed {len(records):,} valid records")
    return records


def extract_year(course):
    """Get the enrollment year from a course code."""
    if "FTT15" in course:
        return 2015
    m = re.search(r'-(\d{2})[A-E]$', course)
    if m:
        return 2000 + int(m.group(1))
    return None


def clean_records(records, log):
    """Apply data cleaning rules."""
    cleaned = []
    dropped = 0
    for r in records:
        if r["city"] == "Tehran" and r["country_raw"] == "IN":
            dropped += 1
            continue

        cc = r["country_raw"]
        if cc == "UK":
            cc = "GB"
        if cc in (",", "") or len(cc) != 2 or not cc.isalpha():
            cc = "US"

        city = r["city"]
        if city.startswith("Chica"):
            cc = "US"
        if city == "Oran":
            cc = "US"

        iso3 = ISO2_TO_ISO3.get(cc)
        if not iso3:
            dropped += 1
            continue

        r["iso3"] = iso3
        cleaned.append(r)

    if dropped > 0:
        log(f"  Dropped {dropped} unrecognized country codes")
    log(f"  After cleaning: {len(cleaned):,} records")
    return cleaned


def build_data(records, log):
    """Build the year-by-year cumulative data structure."""
    by_year = defaultdict(list)
    for r in records:
        by_year[r["year"]].append(r)

    min_year = min(by_year.keys())
    max_year = max(by_year.keys())

    cumul_countries = set()
    cumul_totals = defaultdict(int)
    cumul_users = set()
    cumul_enrollments = 0
    all_names = {}
    data = {}

    for year in range(min_year, max_year + 1):
        year_records = by_year.get(year, [])
        year_countries = set()
        year_users = set()

        prev_countries = set(cumul_countries)

        for r in year_records:
            iso3 = r["iso3"]
            cumul_totals[iso3] += 1
            cumul_countries.add(iso3)
            year_countries.add(iso3)
            cumul_users.add(r["uid"])
            year_users.add(r["uid"])
            if iso3 not in all_names and iso3 in ISO3_TO_NAME:
                all_names[iso3] = ISO3_TO_NAME[iso3]

        cumul_enrollments += len(year_records)
        new_countries = sorted(cumul_countries - prev_countries)

        country_names = {c: ISO3_TO_NAME[c] for c in sorted(cumul_countries) if c in ISO3_TO_NAME}
        new_country_names = {c: ISO3_TO_NAME[c] for c in new_countries if c in ISO3_TO_NAME}

        data[str(year)] = {
            "cumul_countries": sorted(cumul_countries),
            "new_countries": new_countries,
            "country_totals": dict(sorted(cumul_totals.items())),
            "cumul_enrollments": cumul_enrollments,
            "cumul_users": len(cumul_users),
            "cumul_country_count": len(cumul_countries),
            "year_enrollments": len(year_records),
            "year_users": len(year_users),
            "year_country_count": len(year_countries),
            "country_names": country_names,
            "new_country_names": new_country_names,
        }

    log(f"  Built data for {min_year}–{max_year}")
    return data, all_names, min_year, max_year


def update_html_files(data_json, names_json, max_year, log):
    """Find and update HTML map files in the same folder as this script."""
    html_files = [
        f for f in os.listdir(SCRIPT_DIR)
        if f.endswith(".html")
    ]

    if not html_files:
        log("No FTTA HTML files found in this folder!")
        log("Place this script in the same folder as your map HTML files.")
        return []

    updated = []
    for fname in sorted(html_files):
        fpath = os.path.join(SCRIPT_DIR, fname)
        with open(fpath, "r", encoding="utf-8") as f:
            content = f.read()

        original = content

        content = re.sub(
            r'const DATA = \{.*?\};',
            f'const DATA = {data_json};',
            content, count=1
        )
        content = re.sub(
            r'const NAMES = \{.*?\};',
            f'const NAMES = {names_json};',
            content, count=1
        )
        content = re.sub(r'max="\d{4}"', f'max="{max_year}"', content)
        content = re.sub(r'2010–\d{4}', f'2010–{max_year}', content)

        if content != original:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            updated.append(fname)
            log(f"  Updated: {fname}")
        else:
            log(f"  No changes needed: {fname}")

    return updated


def run_update(log, on_complete):
    """Run the full update pipeline. Called from a background thread."""
    try:
        # Step 1: Fetch
        lines = fetch_data(log)

        # Step 2: Parse
        log("\nParsing enrollment records...")
        records = parse_rows(lines, log)

        # Step 3: Clean
        log("\nCleaning data...")
        records = clean_records(records, log)

        if not records:
            on_complete(False, "No valid records found. Check your internet connection.", {})
            return

        # Step 4: Build
        log("\nBuilding cumulative data...")
        data, names, min_year, max_year = build_data(records, log)

        final = data[str(max_year)]
        summary = {
            "enrollments": final["cumul_enrollments"],
            "learners": final["cumul_users"],
            "countries": final["cumul_country_count"],
            "year_range": f"{min_year}–{max_year}",
        }

        # Step 5: Serialize
        data_json = json.dumps(data, separators=(",", ":"))
        names_json = json.dumps(names, separators=(",", ":"))

        # Step 6: Update HTML
        log(f"\nUpdating HTML map files...")
        updated = update_html_files(data_json, names_json, max_year, log)
        summary["files_updated"] = len(updated) if updated else 0
        summary["file_names"] = updated or []

        on_complete(True, "Update complete!", summary)

    except Exception as e:
        on_complete(False, f"Error: {e}", {})


# ═══════════════════════════════════════════════════════════════
#  GUI
# ═══════════════════════════════════════════════════════════════

def launch_gui():
    """Create and run the tkinter GUI."""
    import tkinter as tk
    from tkinter import scrolledtext

    # ── Colors ────────────────────────────────────────────────
    BG          = "#1a2332"
    BG_CARD     = "#223044"
    FG          = "#e8ecf1"
    FG_DIM      = "#8899aa"
    ACCENT      = "#4a90d9"
    ACCENT_HOVER = "#5aa0e9"
    SUCCESS     = "#4caf50"
    ORANGE      = "#ff9800"
    LOG_BG      = "#0d1520"
    LOG_FG      = "#a8b8c8"

    root = tk.Tk()
    root.title("FTTA Online — Map Updater")
    root.configure(bg=BG)
    root.resizable(False, False)

    # Center window
    w, h = 560, 640
    sx = root.winfo_screenwidth() // 2 - w // 2
    sy = root.winfo_screenheight() // 2 - h // 2
    root.geometry(f"{w}x{h}+{sx}+{sy}")

    # ── Title area ────────────────────────────────────────────
    title_frame = tk.Frame(root, bg=BG)
    title_frame.pack(fill="x", padx=24, pady=(20, 0))

    tk.Label(
        title_frame, text="FTTA Online", font=("Helvetica", 22, "bold"),
        bg=BG, fg=FG
    ).pack(anchor="w")
    tk.Label(
        title_frame, text="Global Reach Map Updater", font=("Helvetica", 13),
        bg=BG, fg=FG_DIM
    ).pack(anchor="w")

    # ── Stats cards (hidden until update completes) ───────────
    stats_frame = tk.Frame(root, bg=BG)

    def make_stat_card(parent, label_text, value_text="—"):
        card = tk.Frame(parent, bg=BG_CARD, padx=12, pady=8)
        val = tk.Label(
            card, text=value_text, font=("Helvetica", 20, "bold"),
            bg=BG_CARD, fg=FG
        )
        val.pack()
        tk.Label(
            card, text=label_text, font=("Helvetica", 10),
            bg=BG_CARD, fg=FG_DIM
        ).pack()
        return card, val

    card_enroll, val_enroll = make_stat_card(stats_frame, "Enrollments")
    card_learn, val_learn   = make_stat_card(stats_frame, "Learners")
    card_count, val_count   = make_stat_card(stats_frame, "Countries")
    card_files, val_files   = make_stat_card(stats_frame, "Files Updated")

    card_enroll.pack(side="left", expand=True, fill="both", padx=(0, 4))
    card_learn.pack(side="left", expand=True, fill="both", padx=4)
    card_count.pack(side="left", expand=True, fill="both", padx=4)
    card_files.pack(side="left", expand=True, fill="both", padx=(4, 0))

    # ── Status label ──────────────────────────────────────────
    status_var = tk.StringVar(value="Ready to update")
    status_label = tk.Label(
        root, textvariable=status_var, font=("Helvetica", 11),
        bg=BG, fg=FG_DIM, anchor="w"
    )
    status_label.pack(fill="x", padx=24, pady=(16, 4))

    # ── Log area ──────────────────────────────────────────────
    log_area = scrolledtext.ScrolledText(
        root, height=14, font=("Courier", 11), wrap="word",
        bg=LOG_BG, fg=LOG_FG, insertbackground=LOG_FG,
        relief="flat", borderwidth=0, padx=10, pady=8,
        state="disabled"
    )
    log_area.pack(fill="both", expand=True, padx=24, pady=(0, 8))

    # Custom scrollbar color (best effort)
    log_area.vbar.configure(troughcolor=LOG_BG)

    def log(msg):
        """Thread-safe log append."""
        def _append():
            log_area.configure(state="normal")
            log_area.insert("end", msg + "\n")
            log_area.see("end")
            log_area.configure(state="disabled")
        root.after(0, _append)

    # ── Buttons area ──────────────────────────────────────────
    btn_frame = tk.Frame(root, bg=BG)
    btn_frame.pack(fill="x", padx=24, pady=(0, 20))

    running = [False]

    def on_enter(e):
        if not running[0]:
            btn_bg.configure(bg=ACCENT_HOVER)
            btn_label.configure(bg=ACCENT_HOVER)

    def on_leave(e):
        if not running[0]:
            btn_bg.configure(bg=ACCENT)
            btn_label.configure(bg=ACCENT)

    def on_complete(success, message, summary):
        """Called from background thread when update finishes."""
        def _finish():
            running[0] = False
            if success:
                status_var.set("Update complete!")
                status_label.configure(fg=SUCCESS)

                val_enroll.configure(text=f"{summary['enrollments']:,}")
                val_learn.configure(text=f"{summary['learners']:,}")
                val_count.configure(text=str(summary['countries']))
                val_files.configure(text=str(summary['files_updated']))
                stats_frame.pack(fill="x", padx=24, pady=(16, 0))

                log(f"\n{'=' * 44}")
                log(f"  {summary['enrollments']:,} enrollments")
                log(f"  {summary['learners']:,} unique learners")
                log(f"  {summary['countries']} countries  ({summary['year_range']})")
                log(f"  {summary['files_updated']} file(s) updated")
                log(f"{'=' * 44}")

                btn_label.configure(text="   Update Again   ")
                btn_bg.configure(bg=ACCENT)
                btn_label.configure(bg=ACCENT)

                # Show open-map buttons if files were updated
                if summary.get("file_names"):
                    open_frame = tk.Frame(root, bg=BG)
                    open_frame.pack(fill="x", padx=24, pady=(0, 8))
                    tk.Label(
                        open_frame, text="Open in browser:",
                        font=("Helvetica", 10), bg=BG, fg=FG_DIM
                    ).pack(side="left")
                    for fname in summary["file_names"]:
                        short = fname.replace("FTTA_CumulativeEnrollment_", "").replace(".html", "")
                        def _open(f=fname):
                            webbrowser.open("file://" + os.path.join(SCRIPT_DIR, f))
                        link = tk.Label(
                            open_frame, text=short, font=("Helvetica", 10, "underline"),
                            bg=BG, fg=ORANGE, cursor="hand2"
                        )
                        link.pack(side="left", padx=(8, 0))
                        link.bind("<Button-1>", lambda e, f=fname: _open(f))
            else:
                status_var.set(message)
                status_label.configure(fg=ORANGE)
                btn_label.configure(text="   Retry   ")
                btn_bg.configure(bg=ACCENT)
                btn_label.configure(bg=ACCENT)
                log(f"\n{message}")

        root.after(0, _finish)

    def start_update():
        if running[0]:
            return
        running[0] = True

        # Reset UI
        log_area.configure(state="normal")
        log_area.delete("1.0", "end")
        log_area.configure(state="disabled")
        status_var.set("Updating...")
        status_label.configure(fg=ACCENT)
        btn_label.configure(text="   Updating...   ")
        btn_bg.configure(bg=FG_DIM)
        btn_label.configure(bg=FG_DIM)

        # Hide stats from any previous run
        stats_frame.pack_forget()

        # Remove any previous "open in browser" frames
        for child in root.pack_slaves():
            if isinstance(child, tk.Frame) and child not in (title_frame, btn_frame, stats_frame):
                if child != stats_frame:
                    # Check if it's an open-map frame (has link labels)
                    labels = [w for w in child.winfo_children() if isinstance(w, tk.Label)]
                    if any("Open" in str(l.cget("text")) or l.cget("cursor") == "hand2" for l in labels):
                        child.destroy()

        timestamp = datetime.now().strftime("%b %d, %Y at %I:%M %p")
        log(f"Starting update — {timestamp}\n")

        thread = threading.Thread(target=run_update, args=(log, on_complete), daemon=True)
        thread.start()

    # Use Frame+Label instead of Button so colors render on Mac
    btn_bg = tk.Frame(btn_frame, bg=ACCENT, cursor="hand2")
    btn_bg.pack(fill="x")
    btn_label = tk.Label(
        btn_bg, text="   Update Maps   ", font=("Helvetica", 16, "bold"),
        bg=ACCENT, fg="white", pady=12, cursor="hand2"
    )
    btn_label.pack(fill="x")
    btn_bg.bind("<Button-1>", lambda e: start_update())
    btn_label.bind("<Button-1>", lambda e: start_update())
    btn_bg.bind("<Enter>", on_enter)
    btn_label.bind("<Enter>", on_enter)
    btn_bg.bind("<Leave>", on_leave)
    btn_label.bind("<Leave>", on_leave)

    # ── Folder label at bottom ────────────────────────────────
    tk.Label(
        root, text=f"Folder: {SCRIPT_DIR}", font=("Helvetica", 9),
        bg=BG, fg=FG_DIM, anchor="w"
    ).pack(fill="x", padx=24, pady=(0, 12))

    root.mainloop()


# ═══════════════════════════════════════════════════════════════
#  Entry point — GUI if possible, otherwise CLI fallback
# ═══════════════════════════════════════════════════════════════

def main_cli():
    """Command-line fallback if tkinter isn't available."""
    print("=" * 55)
    print("  FTTA Online — Map Data Updater")
    print("=" * 55)
    print()

    def log(msg):
        print(msg)

    result = {"ok": False}

    def on_complete(success, message, summary):
        print()
        if success:
            print(f"  Summary ({summary['year_range']}):")
            print(f"    Enrollments:  {summary['enrollments']:,}")
            print(f"    Learners:     {summary['learners']:,}")
            print(f"    Countries:    {summary['countries']}")
            print(f"    Files updated: {summary['files_updated']}")
            result["ok"] = True
        else:
            print(f"  {message}")
        print()

    run_update(log, on_complete)
    # Exit non-zero on failure so CI (GitHub Actions) shows the run as failed
    if not result["ok"]:
        sys.exit(1)


if __name__ == "__main__":
    try:
        import tkinter
        launch_gui()
    except ImportError:
        print("(tkinter not available — running in terminal mode)\n")
        main_cli()
