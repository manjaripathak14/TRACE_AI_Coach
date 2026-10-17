# TRACE

TRACE is a Flask-based coding and interview readiness platform built for HackDays 2.0.

## Features

- User registration and login using persistent SQLite storage
- Password hashing with PBKDF2-HMAC-SHA256
- Protected routes with session-based authentication
- Gemini-powered code analysis, interview feedback, roadmap generation, and mock interview summary
- Dashboard and activity tracking using the local SQLite database
- Manual platform profile-link saving for Codeforces, GeeksforGeeks, and LeetCode; public GitHub repositories can be analyzed on request
- JSON profile-link import for Codeforces and GeeksforGeeks, plus LeetCode problem-history import

## Local setup

1. Create a virtual environment:
   `python -m venv .venv`
2. Activate it:
   Windows PowerShell: `.\.venv\Scripts\Activate.ps1`
3. Install dependencies:
   `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in the values.
5. Start the app:
   `python app.py`

## Environment variables

- `FLASK_SECRET_KEY`: secret key for Flask sessions
- `GEMINI_API_KEY`: Google Gemini API key
- `GEMINI_MODEL`: Gemini model available to your API account, default `gemini-3.8-flash`
- `TRACE_DATABASE_PATH`: local SQLite database path
- `FLASK_DEBUG`: set to `1` for debug mode

## Notes

- The project currently stores data in a local SQLite database file named `trace.db` by default.
- The application does not execute user-submitted code. Gemini is used only for analysis and guidance prompts.
- If `GEMINI_API_KEY` is missing, the app shows a clear configuration error instead of failing silently.

## Demo flow

- Register a user at `/register`
- Sign in from the landing page `/`
- Use the dashboard, interview prep, mock interview, and profile pages
- Gemini-powered routes are available through protected API endpoints and UI integration points

## Optional sample database

To create an isolated demo database with a clearly labeled sample profile, activity, and interview plan, run:

```powershell
.\.venv\Scripts\python.exe seed_demo.py
```

The script creates `trace_demo.db` without modifying the normal `trace.db`. It prints a randomly generated demo password once; save it if you want to sign in as `trace-demo`. To launch TRACE against the demo database for the current PowerShell window:

```powershell
$env:TRACE_DATABASE_PATH = "trace_demo.db"
.\.venv\Scripts\python.exe app.py
```

The sample records are illustrative only; they are not real user analytics or Gemini-generated results. Running the seeder again against the same database will not duplicate or overwrite the demo account.

## Platform JSON import

On Connected Platforms, LeetCode's **Import History JSON** accepts a JSON array of problem records with `ID`, `Title`, `Difficulty`, `URL`, `Timestamp`, and `Submissions` fields, as in a LeetCode problem-history export. Valid records are saved to the signed-in user's Coding History page. Duplicate records are skipped. Other platform JSON imports can save a profile URL when the file contains a matching profile URL or username.

```json
[
  {
    "ID": "222",
    "Title": "Count Complete Tree Nodes",
    "Difficulty": "Medium",
    "URL": "https://leetcode.com/problems/count-complete-tree-nodes/",
    "Timestamp": "2025-01-15T10:30:00Z",
    "Submissions": "2"
  }
]
```

The dashboard growth graph uses timestamps such as `YYYY-MM-DD`, ISO date-times, or LeetCode dates such as `Oct 7` to plot cumulative solved problems over time. For dates without a year, the year is inferred from when the history record was imported. Unrecognized timestamps still appear in Coding History and dashboard totals, but are omitted from the graph. The import stores user-provided records only. It does not authenticate with LeetCode, verify the data, or establish a live account connection. Files are limited to 1 MB and 5,000 problem records.

## Public GitHub repository analysis

On **Connect Platforms**, paste a public `https://github.com/owner/repository` URL into any profile URL field and save it. The repository is listed separately from platform profiles. Select **Analyze with Gemini** to fetch up to 12 text-based source files (up to 12 KB per file and 48 KB total) from the repository's default branch and request a coding-learning profile. Files are sent to Gemini only after the user confirms the action. TRACE does not execute repository code.

Analysis results appear on **Gemini Analysis** and the latest learning topics inform interview roadmaps, generated mock questions, answer feedback, and mock-session summaries. The analysis does not detect recurring mistakes, verify code authorship, or measure actual runtime or memory; any Big-O complexity is a static estimate based on the sampled code. It is based on a limited source sample and includes data limitations. GitHub's unauthenticated API rate limits apply. Private repositories are not supported; do not provide a GitHub password or token.
