# Focus history, categories and statistics — proposal for review

Status: proposal only. No categories, statistics storage, goals or tracker controls
have been implemented. This document needs user feedback before implementation.

## Experience and organization

Keep the compact sidebar and calm session screen. Add **Statistics** between
Presets and Stop log. The existing permanent Stop log keeps its accountability
purpose; Statistics includes ordinary completed sessions as well as early stops.

On the main Session screen, add a compact **Categories** area with colored labels
and a Manage action. Create, rename, recolor and archive categories here. Suggested
examples are Academic, Work and Hobby; do not create them without the user choosing
them. A category has a stable identity, a unique name and a color from an accessible
palette. Show its name wherever color is used, including charts and tooltips.

Session setup gains a Category selector, initially **Uncategorized**. Category
selection does not change website/app rules. It is a separate choice for each new
session; loading a blocking preset does not silently select a category. Stop and
edit preserves the current draft's category. Once a session starts, its category
is fixed. Rename/recolor updates its presentation in history; archiving preserves
history and removes the category from new-session choices. Uncategorized uses a
neutral color and cannot be deleted.

The active screen shows a small category label below the timer. Category
management and statistics remain accessible while blocking continues. They cannot
alter the current session or release restrictions.

## Statistics screen

At the top: **Day / Week / Month / Year**, previous/next period, Today, a readable
date range, and an All categories/category filter. Default to the current week.
Use calendar periods, not rolling 7/30/365-day windows. Week begins Monday by
default, with a Sunday option in statistics settings.

Below that, show a small summary, a chart, daily goals and a session history list.

| View | Main chart | Supporting detail |
| --- | --- | --- |
| Day | Timeline of focus intervals, colored by category | Goal progress and today's sessions |
| Week | Seven daily bars, stacked by category | Totals and comparison with the previous week |
| Month | Daily bars and a compact calendar | Active days and daily goal results |
| Year | Twelve monthly bars and a calendar heatmap | Annual category totals and streak history |

Chart selection opens the corresponding day/month. Every chart also has a textual
summary or table, keyboard access and a legend. On small windows, stack the
sections vertically. Reduced-motion preferences suppress decorative animation.

Recommended statistics:

- **Focus time:** credited time in the selected period, with a category breakdown.
- **Completed sessions:** sessions that reached their scheduled end, ending in the
  selected period. Show early stops and setup cancellations separately.
- **Average completed session:** average credited duration of those completed
  sessions, calculated from each whole session. Label this clearly because a
  session crossing midnight can contribute time to more than one day's chart.
- **Completion rate:** completed / (completed + password-ended) sessions ending
  in the period. Exclude in-progress sessions and setup cancellations. Show “—”
  when there are no eligible sessions.
- **Active days:** days with at least one credited minute. For week/month/year,
  also show average daily focus across elapsed calendar days, including zero days.
- **Goal consistency:** days on which each category's active goal was met, current
  streak and longest streak. An unfinished today is marked In progress.

For an unfinished period, compare with the same elapsed portion of the previous
period, and label the comparison. If the prior value is zero, show the absolute
change without an infinite percentage. Do not make a single productivity score.

The history list shows start time, category, credited focus, planned duration and
outcome: Completed, Ended early, Cancelled during setup, or In progress. Session
details explain any excluded break/sleep time or missing recovery interval.
Password-ended sessions link to their existing permanent stop record; reasons do
not appear in chart tooltips. Date/category/outcome filters and bounded pagination
keep years of history usable. Offer CSV/JSON export of the selected history and
summaries, clearly indicating whether stop reasons are included (off by default).

## What earns focus credit — proposed defaults

The existing blocking timer still includes sleep and powered-off time. Statistics
should distinguish that elapsed restriction time from time credited toward goals.

Proposed credit is **awake time while the session is active**, excluding future
breaks and powered-off/suspended time. Screen lock or lack of keyboard input alone
does not remove credit: the user may be reading, thinking or working on paper.
There is no screen capture, browsing history collection, or activity surveillance.
Describe the figures as timer-based focus, not verified productive work.

- A normal completion earns its credited time, including valid sessions shorter
  than the suggested five-minute minimum.
- A password-authorized early stop earns time already focused, but is not counted
  as a completed session.
- Stop and edit during the correction window is recorded as a setup cancellation
  with **zero goal/statistics credit** by default. Show its elapsed time in details
  so it is not confused with a failed start. Failed activation earns no credit.
- An active session contributes live, provisional progress; completed-session
  counts and averages update after it ends.
- Retain seconds internally. Goals use exact seconds; rounding the displayed
  minutes must never grant a goal early.
- A session crossing midnight splits credit across the two local calendar days.
  A 23:50–00:20 session can contribute ten minutes to one day and twenty to the next.
- If a crash leaves an unverified interval, credit only durably recorded time and
  visibly mark the gap. Do not invent historical focus time from stop-log entries.

Initialize a **reporting time zone** from the computer's IANA zone. Keep that zone
fixed when the system changes zone during travel, so streak boundaries do not move
silently. A deliberate reporting-zone change should apply to future days and show
its effective date; past daily records retain their original zone. Daylight-saving
transitions use actual elapsed seconds, including 23/25-hour days. Clock changes
must not create duplicate credit; exact recovery behavior needs dedicated tests.

## Daily category goals and streaks

One optional daily goal per named category: **“At least X minutes of focus in Y
per day.”** X is a whole number from 1 to 1,440. These are every-day goals in this
version; weekly quotas, weekday-only schedules and streak freezes are deferred.
Different categories have independent progress and streaks.

Create a goal from Manage categories or the Statistics goals area. A new goal can
start today, including already credited time today, or tomorrow. No days before
its effective date count as misses. Editing the target takes effect tomorrow;
past daily results retain the target that applied on those days. Disabling or
archiving a goal preserves its history and ends that goal's streak series;
reactivating starts a new series. Archiving a category also ends its active goal.

A goal is met when that day's category credit reaches X × 60 seconds. Its current
streak is consecutive goal-met days, including today if met. While today is still
open, yesterday's streak remains visible with “Today's goal in progress.” A missed
day breaks the streak at the next midnight. Longest streak remains in history.
If goal tracking begins today, there is no penalty for earlier days.

The proposed playful visual is a small **focus garden**: one plant per goal, using
the category color. It grows as today's progress increases and blooms when the
goal is reached. Alongside it show “35 / 45 min,” a labeled progress ring, and
“6-day streak.” A seven-day strip uses checkmarks and labels for past results.
Extra focus still appears numerically after the plant blooms. Missing a day does
not kill a plant or use shaming copy. All information is available without color
or animation.

## Data and implementation plan — after approval

The privileged service remains authoritative for session timing and durable
records. Add versioned SQLite migrations for category identities, session history,
credited time segments and dated goal definitions. Daily summaries are derived
from those records and can be rebuilt; they are not the sole source of truth.
Keep the append-only emergency-stop log and its write-before-release guarantee.

Record activation, natural completion, cancellation, early stop and recovery.
Use bounded periodic checkpoints and Linux clocks to distinguish awake time from
suspend without depending on the GTK window. A statistics write failure should
produce a visible recording error, never prolong blocking or prevent natural
expiry. Final statistics reconciliation must not overwrite an existing stop reason.

Read queries and exports use bounded pages and consistent snapshots. History
stores stable category IDs; the proposed first version does not support editing
past session categories, durations or outcomes. Retained data survives ordinary
uninstall; no automatic deletion or cloud transmission is introduced. New history
starts when this feature is installed. Older emergency-stop records remain in
Stop log, with no fabricated completed-session history.

**No Firefox extension change is expected for this feature.** Categories, goals
and statistics belong to the desktop/service and are not sent to the browser.
Existing Firefox break preparation will be used only when desktop breaks are
separately authorized and implemented.

Implementation order after feedback:

1. Agree on credit, calendar and goal semantics; define migration and recovery tests.
2. Implement durable history and category/goal service APIs, preserving timer safety.
3. Add category management and the session-start selector.
4. Add accessible statistics charts, goal garden, history and exports.
5. Test midnight/DST, sleep, crash/reboot, early stops, setup cancellations, goal
   edits, category archives, large history and migration with existing stop logs.
6. Validate on Fedora in light/dark mode, at both display scales and with keyboard
   and assistive technology; review the implementation before delivery.

## Inspiration and decisions for review

TickTick's documented focus statistics include focus duration, period comparisons,
time allocation and goal completion. Those inform the compact overview here.
[TickTick focus overview](https://blog.ticktick.com/2020/05/06/brand-new-focus-experience-ticktick/).
Toggl's detailed reporting uses date ranges, filters and individual time entries;
those inform the drill-down history, rather than its billing features.
[Toggl detailed report](https://support.toggl.com/en-us/article/detailed-report-k9bzy2/).
The garden and exact counting rules above are proposals for this application.

The main decisions to review are: exclude sleep from goal credit; give setup
cancellations zero credit; fix categories once a session begins; make target edits
effective tomorrow; and use the garden visual. Everything in this document is
open to feedback. No implementation should start until that feedback is received.
